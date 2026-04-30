"""下载政策网页中的 PDF 附件，并生成可供搜索页使用的附件清单。

很多政府或交易中心官网会把正文放在网页中，但真正的规则文件放在页面里的
PDF 附件链接中。这个脚本扫描已入库资料的本地 HTML，发现 PDF 链接后下载到
01_原始资料/PDF附件，并把本地路径回填到政策资料台账。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
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
from typing import Any

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
BACKUP_DIR = ROOT / "02_元数据" / "备份"
ATTACHMENT_CSV = ROOT / "02_元数据" / "PDF附件清单.csv"
OUTPUT_JSON = ROOT / "05_输出成果" / "pdf_attachments.json"
PDF_ROOT = ROOT / "01_原始资料" / "PDF附件"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

ATTACHMENT_FIELDS = [
    "资料编号",
    "文件标题",
    "附件标题",
    "附件URL",
    "本地PDF路径",
    "来源页面",
    "下载状态",
    "文件大小",
    "采集时间",
    "备注",
]


class PdfLinkParser(HTMLParser):
    """只提取 a 标签中的 PDF 链接，链接文字用作附件标题。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        href = attr_map.get("href") or attr_map.get("oldsrc") or ""
        if not looks_like_pdf(href):
            return
        self._current = {"href": href, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._current is not None and data.strip():
            self._current["parts"].append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current is None:
            return
        title = normalize_space(" ".join(self._current["parts"]))
        self.links.append({"href": self._current["href"], "title": title})
        self._current = None


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_attachment_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ATTACHMENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def backup(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = BACKUP_DIR / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    return target


def split_paths(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def normalize_path(value: str) -> str:
    return value.replace("/", "\\").strip()


def append_local_path(row: dict[str, str], rel_path: str) -> bool:
    paths = split_paths(row.get("本地文件路径", ""))
    normalized = normalize_path(rel_path)
    if any(normalize_path(item) == normalized for item in paths):
        return False
    row["本地文件路径"] = "; ".join([*paths, rel_path])
    return True


def root_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def local_paths(row: dict[str, str]) -> list[Path]:
    paths: list[Path] = []
    for item in split_paths(row.get("本地文件路径", "")):
        path = ROOT / item
        if path.exists():
            paths.append(path)
    return paths


def html_path(row: dict[str, str]) -> Path | None:
    for path in local_paths(row):
        if path.suffix.lower() in {".html", ".htm"}:
            return path
    return None


def existing_pdf_paths(row: dict[str, str]) -> list[Path]:
    return [path for path in local_paths(row) if path.suffix.lower() == ".pdf"]


def is_http_url(value: str) -> bool:
    return bool(re.match(r"^https?://", (value or "").strip(), flags=re.I))


def looks_like_pdf(href: str) -> bool:
    if not href or href.startswith(("javascript:", "#", "mailto:")):
        return False
    parsed = urllib.parse.urlparse(html.unescape(href.strip()))
    path = urllib.parse.unquote(parsed.path).lower()
    return path.endswith(".pdf") or ".pdf" in path


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def sanitize_filename(value: str, fallback: str) -> str:
    value = normalize_space(value) or fallback
    value = re.sub(r"[\\/:*?\"<>|;；]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:90].strip("._") or fallback


def attachment_filename(doc_id: str, index: int, title: str, url: str) -> str:
    parsed_name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).stem
    fallback = parsed_name or hashlib.sha1(url.encode("utf-8")).hexdigest()[:10].upper()
    stem = sanitize_filename(title, fallback)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8].upper()
    return f"{sanitize_filename(doc_id, 'DOC')}_{index:02d}_{stem}_{digest}.pdf"


def decode_bytes(data: bytes, content_type: str) -> str:
    candidates: list[str] = []
    match = re.search(r"charset=([\w-]+)", content_type or "", flags=re.I)
    if match:
        candidates.append(match.group(1))
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="ignore")


def fetch(url: str, timeout: int, referer: str = "") -> tuple[bytes, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,application/octet-stream,text/html,*/*",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            context = ssl._create_unverified_context()
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    return response.read(), response.headers.get("Content-Type", "")
            except Exception:
                pass
        return fetch_with_curl(url, timeout=timeout, referer=referer)
    except Exception:
        return fetch_with_curl(url, timeout=timeout, referer=referer)


def fetch_with_curl(url: str, timeout: int, referer: str = "") -> tuple[bytes, str]:
    if not shutil.which("curl.exe"):
        raise OSError("curl.exe not found")
    command = [
        "curl.exe",
        "-k",
        "-L",
        "--compressed",
        "--max-time",
        str(max(timeout, 1)),
        "-A",
        USER_AGENT,
    ]
    if referer:
        command.extend(["-e", referer])
    command.append(url)
    result = subprocess.run(command, check=False, capture_output=True, timeout=timeout + 8)
    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        raise OSError(f"curl fallback failed: {stderr or result.returncode}")
    return result.stdout, ""


def extract_pdf_links(raw_html: str, base_url: str) -> list[dict[str, str]]:
    parser = PdfLinkParser()
    parser.feed(raw_html)
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for link in parser.links:
        href = html.unescape(link["href"]).strip()
        absolute_url = urllib.parse.urljoin(base_url, href) if base_url else href
        if not is_http_url(absolute_url) or absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append({"url": absolute_url, "title": link["title"]})
    return links


def read_html(row: dict[str, str], timeout: int, fetch_missing_pages: bool) -> tuple[str, str]:
    path = html_path(row)
    if path:
        return path.read_text(encoding="utf-8", errors="ignore"), row.get("原文链接", "")
    url = row.get("原文链接", "").strip()
    if fetch_missing_pages and is_http_url(url) and not looks_like_pdf(url):
        data, content_type = fetch(url, timeout=timeout)
        return decode_bytes(data, content_type), url
    return "", url


def is_pdf_bytes(data: bytes, content_type: str) -> bool:
    stripped = data[:1024].lstrip()
    return stripped.startswith(b"%PDF-") or "pdf" in (content_type or "").lower()


def existing_entry(row: dict[str, str], path: Path, collected_at: str) -> dict[str, str]:
    return {
        "资料编号": row.get("资料编号", ""),
        "文件标题": row.get("文件标题", ""),
        "附件标题": path.name,
        "附件URL": row.get("原文链接", "") if looks_like_pdf(row.get("原文链接", "")) else "",
        "本地PDF路径": root_relative(path),
        "来源页面": row.get("原文链接", ""),
        "下载状态": "existing_local_pdf",
        "文件大小": str(path.stat().st_size),
        "采集时间": collected_at,
        "备注": "台账中已有本地PDF",
    }


def download_for_row(row: dict[str, str], args: argparse.Namespace, collected_at: str) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
    entries = [existing_entry(row, path, collected_at) for path in existing_pdf_paths(row)]
    failures: list[dict[str, str]] = []
    changed = False
    entry_by_path = {normalize_path(entry["本地PDF路径"]): entry for entry in entries}

    raw_html, base_url = read_html(row, timeout=args.timeout, fetch_missing_pages=args.fetch_missing_pages)
    if not raw_html:
        return entries, failures, changed

    links = extract_pdf_links(raw_html, base_url)
    existing_urls = {entry.get("附件URL", "") for entry in entries if entry.get("附件URL")}
    existing_paths = {normalize_path(entry.get("本地PDF路径", "")) for entry in entries}
    for link_index, link in enumerate(links, start=1):
        url = link["url"]
        if url in existing_urls:
            continue
        title = link["title"] or Path(urllib.parse.urlparse(url).path).name or "PDF附件"
        filename = attachment_filename(row.get("资料编号", ""), link_index, title, url)
        target = PDF_ROOT / filename
        rel_target = root_relative(target)
        normalized_target = normalize_path(rel_target)
        if normalized_target in existing_paths:
            matched_entry = entry_by_path.get(normalized_target)
            if matched_entry:
                matched_entry["附件标题"] = title
                matched_entry["附件URL"] = url
                matched_entry["来源页面"] = base_url
                matched_entry["备注"] = "台账中已有本地PDF；已匹配网页附件链接"
            existing_urls.add(url)
            continue

        status = "downloaded"
        note = ""
        size = 0
        try:
            if target.exists() and not args.force:
                status = "existing_download"
                size = target.stat().st_size
            elif args.dry_run:
                status = "dry_run"
            else:
                data, content_type = fetch(url, timeout=args.timeout, referer=base_url)
                if not is_pdf_bytes(data, content_type):
                    raise ValueError(f"返回内容不像PDF：{content_type or 'unknown'}")
                PDF_ROOT.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                size = target.stat().st_size
                if args.delay:
                    time.sleep(args.delay)

            if not args.dry_run and normalized_target not in existing_paths:
                changed = append_local_path(row, rel_target) or changed
                existing_paths.add(normalized_target)

            entry = {
                "资料编号": row.get("资料编号", ""),
                "文件标题": row.get("文件标题", ""),
                "附件标题": title,
                "附件URL": url,
                "本地PDF路径": rel_target,
                "来源页面": base_url,
                "下载状态": status,
                "文件大小": str(size),
                "采集时间": collected_at,
                "备注": note,
            }
            entries.append(entry)
            entry_by_path[normalized_target] = entry
            existing_urls.add(url)
        except Exception as exc:
            failure = {
                "资料编号": row.get("资料编号", ""),
                "文件标题": row.get("文件标题", ""),
                "附件标题": title,
                "附件URL": url,
                "本地PDF路径": rel_target,
                "来源页面": base_url,
                "下载状态": "failed",
                "文件大小": "0",
                "采集时间": collected_at,
                "备注": str(exc),
            }
            failures.append(failure)
            entries.append(failure)
            if args.delay:
                time.sleep(args.delay)

    return entries, failures, changed


def write_manifest(entries: list[dict[str, str]], failures: list[dict[str, str]], collected_at: str, dry_run: bool) -> tuple[Path, Path]:
    usable_entries = [entry for entry in entries if entry.get("下载状态") != "failed"]
    output_json = OUTPUT_JSON.with_name("pdf_attachments.dry_run.json") if dry_run else OUTPUT_JSON
    attachment_csv = ATTACHMENT_CSV.with_name("PDF附件清单_dry_run.csv") if dry_run else ATTACHMENT_CSV
    output_json.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": collected_at,
        "count": len(usable_entries),
        "failure_count": len(failures),
        "entries": usable_entries,
        "failures": failures,
    }
    output_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    write_attachment_csv(attachment_csv, entries)
    return attachment_csv, output_json


def self_check(entries: list[dict[str, str]], dry_run: bool) -> None:
    if dry_run:
        return
    for entry in entries:
        if entry.get("下载状态") == "failed":
            continue
        rel_path = entry.get("本地PDF路径", "")
        if not rel_path:
            continue
        path = ROOT / rel_path
        if not path.exists() or path.stat().st_size <= 0:
            raise AssertionError(f"PDF附件缺失或为空：{path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载政策网页中的 PDF 附件并生成清单")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条台账记录，便于试跑")
    parser.add_argument("--only-id", default="", help="只处理指定资料编号")
    parser.add_argument("--timeout", type=int, default=25, help="单个请求超时时间")
    parser.add_argument("--delay", type=float, default=0.25, help="下载间隔秒数")
    parser.add_argument("--force", action="store_true", help="重新下载已存在附件")
    parser.add_argument("--dry-run", action="store_true", help="只发现链接，不下载PDF、不改台账；发现清单写入 dry_run 文件")
    parser.add_argument("--fetch-missing-pages", action="store_true", help="没有本地HTML时访问原文页面寻找附件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, fields = read_rows(LEDGER_CSV)
    if args.only_id:
        rows = [row for row in rows if row.get("资料编号", "") == args.only_id]
    if args.limit:
        rows = rows[: args.limit]

    collected_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    changed = False

    for index, row in enumerate(rows, start=1):
        entries, row_failures, row_changed = download_for_row(row, args, collected_at)
        all_entries.extend(entries)
        failures.extend(row_failures)
        changed = changed or row_changed
        if index == 1 or index % 25 == 0 or index == len(rows):
            print(f"[{index}/{len(rows)}] PDF entries={len(all_entries)} failures={len(failures)}", flush=True)

    if changed and not args.dry_run:
        backup_path = backup(LEDGER_CSV)
        all_rows, _ = read_rows(LEDGER_CSV)
        changed_by_id = {row.get("资料编号", ""): row for row in rows}
        merged_rows = [changed_by_id.get(row.get("资料编号", ""), row) for row in all_rows]
        write_rows(LEDGER_CSV, merged_rows, fields)
        print(f"备份文件：{backup_path}")

    attachment_csv, output_json = write_manifest(all_entries, failures, collected_at, dry_run=args.dry_run)
    self_check(all_entries, dry_run=args.dry_run)

    downloaded = sum(1 for entry in all_entries if entry.get("下载状态") == "downloaded")
    existing = sum(1 for entry in all_entries if entry.get("下载状态", "").startswith("existing"))
    print(f"PDF附件清单：{attachment_csv}")
    print(f"PDF附件索引：{output_json}")
    print(f"PDF附件目录：{PDF_ROOT}")
    print(f"发现可用PDF：{len(all_entries) - len(failures)}，新下载：{downloaded}，已存在：{existing}，失败：{len(failures)}")

    append_log(
        action_type="PDF附件下载",
        content=f"扫描政策网页PDF附件，可用{len(all_entries) - len(failures)}个，新下载{downloaded}个，失败{len(failures)}个",
        files=f"{attachment_csv.relative_to(ROOT)}; {output_json.relative_to(ROOT)}; {PDF_ROOT.relative_to(ROOT)}; {LEDGER_CSV.relative_to(ROOT)}",
        command=" ".join([str(Path(sys.executable)), *sys.argv]),
        result="完成" if not failures else "部分失败",
        note="dry-run模式未写文件" if args.dry_run else "",
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
