"""为政策资料生成截图式网页快照。

这个脚本不重新爬取官网，而是使用已经落盘到 01_原始资料 的 HTML/PDF/文本
生成本地截图证据。搜索页仍打开一个轻量查看页，但查看页主体展示的是截图图片，
避免官网链接失效后只剩处理后文本。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont

os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - 自检环境会安装 playwright。
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
SNAPSHOT_DIR = OUTPUT_DIR / "网页快照"
IMAGE_DIR = SNAPSHOT_DIR / "images"
MANIFEST_PATH = SNAPSHOT_DIR / "snapshot_manifest.json"

HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".txt"}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_filename(value: str, fallback: str) -> str:
    value = (value or "").strip() or fallback
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    return value[:120].strip("._") or fallback


def split_paths(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def choose_local_files(row: dict[str, str]) -> tuple[Path | None, Path | None]:
    raw_path: Path | None = None
    text_path: Path | None = None
    for item in split_paths(row.get("本地文件路径", "")):
        candidate = ROOT / item
        if not candidate.exists():
            continue
        if candidate.suffix.lower() in TEXT_SUFFIXES and text_path is None:
            text_path = candidate
        elif raw_path is None:
            raw_path = candidate
    return raw_path, text_path


def relative_href(from_dir: Path, target: Path) -> str:
    try:
        rel = target.relative_to(from_dir)
    except ValueError:
        try:
            rel = Path("..") / ".." / target.relative_to(ROOT)
        except ValueError:
            rel = target
    value = rel.as_posix()
    return urllib.parse.quote(value, safe="/._-()[]{}~!$&'()*+,;=:@")


def root_relative(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_text_preview(path: Path | None, limit: int) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def doc_id_for(row: dict[str, str]) -> str:
    doc_id = row.get("资料编号", "").strip()
    if doc_id:
        return safe_filename(doc_id, "snapshot")
    seed = row.get("原文链接", "") or row.get("文件标题", "")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"SNAPSHOT-{digest}"


def viewer_path_for(row: dict[str, str]) -> Path:
    return SNAPSHOT_DIR / f"{doc_id_for(row)}.html"


def image_path_for(row: dict[str, str]) -> Path:
    return IMAGE_DIR / f"{doc_id_for(row)}.jpg"


def find_edge_executable() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((path for path in candidates if path.exists()), None)


def launch_browser(browser_channel: str, browser_executable: str) -> tuple[Any | None, Any | None]:
    if sync_playwright is None:
        return None, None

    manager = sync_playwright().start()
    try:
        kwargs: dict[str, Any] = {"headless": True}
        if browser_executable:
            kwargs["executable_path"] = browser_executable
        elif browser_channel:
            kwargs["channel"] = browser_channel
        browser = manager.chromium.launch(**kwargs)
        return manager, browser
    except Exception:
        edge_path = find_edge_executable()
        if edge_path and not browser_executable:
            browser = manager.chromium.launch(executable_path=str(edge_path), headless=True)
            return manager, browser
        manager.stop()
        raise


def render_html_screenshot(page: Any, source_path: Path, image_path: Path, args: argparse.Namespace) -> None:
    page.set_viewport_size({"width": args.width, "height": args.viewport_height})
    page.goto(source_path.resolve().as_uri(), wait_until="commit", timeout=args.timeout_ms)
    if args.idle_timeout_ms > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=args.idle_timeout_ms)
        except PlaywrightTimeoutError:
            pass
    if args.render_wait_ms > 0:
        page.wait_for_timeout(args.render_wait_ms)

    page.add_style_tag(
        content="""
        html, body { background: #ffffff !important; font-family: "Microsoft YaHei", Arial, sans-serif !important; }
        * { font-family: "Microsoft YaHei", Arial, sans-serif !important; }
        img, table { max-width: 100%; }
        """
    )
    page.evaluate(
        """() => {
            try {
                if (document.fonts && document.fonts.clear) {
                    document.fonts.clear();
                }
            } catch (error) {}
        }"""
    )
    page.screenshot(
        path=str(image_path),
        type="jpeg",
        quality=args.quality,
        full_page=args.full_page,
        timeout=args.timeout_ms,
    )


def resize_to_width(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image
    height = int(image.height * width / image.width)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def render_pdf_screenshot(source_path: Path, image_path: Path, args: argparse.Namespace) -> None:
    doc = fitz.open(source_path)
    if doc.page_count == 0:
        raise ValueError("PDF 没有可渲染页面")

    pages: list[Image.Image] = []
    for index in range(min(args.pdf_pages, doc.page_count)):
        page = doc.load_page(index)
        pix = page.get_pixmap(matrix=fitz.Matrix(args.pdf_zoom, args.pdf_zoom), alpha=False)
        page_image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        pages.append(resize_to_width(page_image, args.width))

    gap = 22
    total_height = sum(page.height for page in pages) + gap * (len(pages) + 1)
    canvas = Image.new("RGB", (args.width + gap * 2, total_height), "#f4f5f7")
    y = gap
    for page_image in pages:
        x = (canvas.width - page_image.width) // 2
        canvas.paste(page_image, (x, y))
        y += page_image.height + gap
    canvas.save(image_path, "JPEG", quality=args.quality, optimize=True)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_names = [
        "msyhbd.ttc" if bold else "msyh.ttc",
        "simhei.ttf" if bold else "simsun.ttc",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    font_dirs = [
        Path(r"C:\Windows\Fonts"),
        Path(r"C:\Windows\winsxs"),
    ]
    for font_dir in font_dirs:
        for name in font_names:
            candidate = font_dir / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            probe = current + char
            if draw.textbbox((0, 0), probe, font=font)[2] <= max_width:
                current = probe
                continue
            if current:
                lines.append(current)
            current = char
        if current:
            lines.append(current)
    return lines


def render_text_screenshot(
    row: dict[str, str],
    text_path: Path | None,
    image_path: Path,
    generated_at: str,
    args: argparse.Namespace,
    note: str = "",
) -> None:
    width = args.width
    margin = 54
    content_width = width - margin * 2
    title_font = load_font(30, bold=True)
    meta_font = load_font(18)
    body_font = load_font(20)
    small_font = load_font(15)

    scratch = Image.new("RGB", (width, 800), "#ffffff")
    draw = ImageDraw.Draw(scratch)
    title = row.get("文件标题", "") or "未命名政策"
    body = read_text_preview(text_path, args.text_limit) or "未找到可渲染的网页/PDF，且处理后文本为空。"
    metadata = [
        f"资料编号：{row.get('资料编号', '-') or '-'}",
        f"发布机构：{row.get('发布部门', '-') or '-'}",
        f"发布日期：{row.get('发布日期', '-') or '-'}",
        f"文号：{row.get('文号', '-') or '-'}",
        f"截图生成：{generated_at}",
    ]
    if note:
        metadata.append(f"生成说明：{note}")

    title_lines = wrap_text(draw, title, title_font, content_width)
    body_lines = wrap_text(draw, body, body_font, content_width)
    if len(body_lines) > args.text_max_lines:
        body_lines = body_lines[: args.text_max_lines] + ["……"]

    line_height = 34
    height = (
        margin * 2
        + len(title_lines) * 42
        + 28
        + len(metadata) * 28
        + 42
        + len(body_lines) * line_height
        + 24
    )
    height = max(height, 900)
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    y = margin
    draw.text((margin, y), "截图式网页快照", fill="#5f6b7a", font=small_font)
    y += 32
    for line in title_lines:
        draw.text((margin, y), line, fill="#17202a", font=title_font)
        y += 42
    y += 8
    draw.line((margin, y, width - margin, y), fill="#d8dee7", width=2)
    y += 24
    for item in metadata:
        draw.text((margin, y), item, fill="#46515f", font=meta_font)
        y += 28
    y += 18
    draw.text((margin, y), "正文预览", fill="#17202a", font=meta_font)
    y += 34
    for line in body_lines:
        draw.text((margin, y), line, fill="#1f2933", font=body_font)
        y += line_height

    image.save(image_path, "JPEG", quality=args.quality, optimize=True)


def build_viewer_html(
    row: dict[str, str],
    raw_path: Path | None,
    text_path: Path | None,
    image_path: Path,
    viewer_path: Path,
    generated_at: str,
    method: str,
    message: str,
) -> str:
    title = row.get("文件标题", "") or "未命名政策"
    image_href = relative_href(viewer_path.parent, image_path)
    raw_href = relative_href(viewer_path.parent, raw_path) if raw_path else ""
    text_href = relative_href(viewer_path.parent, text_path) if text_path else ""
    original_url = row.get("原文链接", "")
    metadata = [
        ("资料编号", row.get("资料编号", "")),
        ("发布机构", row.get("发布部门", "")),
        ("采集来源机构", row.get("采集来源机构", "")),
        ("发布日期", row.get("发布日期", "")),
        ("文号", row.get("文号", "")),
        ("适用地区", row.get("适用地区", "")),
        ("市场主题", row.get("市场主题", "")),
        ("来源类型", row.get("来源类型", "")),
        ("权威等级", row.get("权威等级", "")),
        ("有效状态", row.get("有效状态", "")),
        ("截图方式", method),
        ("快照生成时间", generated_at),
    ]
    if message:
        metadata.append(("生成说明", message))
    metadata_html = "\n".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(value or '-')}</dd>" for label, value in metadata
    )
    original_link = (
        f'<a href="{html.escape(original_url)}" target="_blank" rel="noreferrer">官网原文</a>'
        if original_url
        else '<span class="disabled">官网原文缺失</span>'
    )
    raw_link = (
        f'<a href="{html.escape(raw_href)}" target="_blank" rel="noreferrer">本地原始文件</a>'
        if raw_href
        else '<span class="disabled">本地原始文件缺失</span>'
    )
    text_link = (
        f'<a href="{html.escape(text_href)}" target="_blank" rel="noreferrer">处理后文本</a>'
        if text_href
        else '<span class="disabled">处理后文本缺失</span>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - 截图快照</title>
  <style>
    :root {{
      --bg: #eef1f5;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #5f6b7a;
      --line: #d8dee7;
      --blue: #1f6feb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.55;
    }}
    header {{
      padding: 18px 22px 14px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 21px;
      line-height: 1.35;
    }}
    .hint {{
      color: var(--muted);
      font-size: 13px;
    }}
    main {{
      display: grid;
      grid-template-columns: 330px minmax(0, 1fr);
      gap: 0;
      min-height: calc(100vh - 86px);
    }}
    aside {{
      padding: 16px;
      border-right: 1px solid var(--line);
      background: #fbfcfd;
    }}
    dl {{
      margin: 0;
      display: grid;
      grid-template-columns: 98px minmax(0, 1fr);
      gap: 8px 10px;
      font-size: 13px;
    }}
    dt {{ color: var(--muted); }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 16px 0;
    }}
    .actions a, .disabled {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 9px;
      background: var(--panel);
      color: var(--blue);
      text-decoration: none;
      font-size: 13px;
    }}
    .disabled {{ color: var(--muted); }}
    .path {{
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 12px;
    }}
    .viewer {{
      padding: 18px;
      overflow: auto;
    }}
    .snapshot-image {{
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
      border: 1px solid var(--line);
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.14);
    }}
    @media (max-width: 860px) {{
      main {{ display: block; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="hint">这是本地保存资料生成的截图式网页快照；官网链接失效时，仍可查看截图证据。</div>
  </header>
  <main>
    <aside>
      <dl>{metadata_html}</dl>
      <div class="actions">
        <a href="{html.escape(image_href)}" target="_blank" rel="noreferrer">打开截图</a>
        {original_link}
        {raw_link}
        {text_link}
      </div>
      <div class="path">
        <div>截图文件：{html.escape(root_relative(image_path))}</div>
        <div>原始文件：{html.escape(root_relative(raw_path) or "-")}</div>
        <div>处理后文本：{html.escape(root_relative(text_path) or "-")}</div>
      </div>
    </aside>
    <section class="viewer">
      <img class="snapshot-image" src="{html.escape(image_href)}" alt="截图快照">
    </section>
  </main>
</body>
</html>
"""


def build_snapshots(args: argparse.Namespace) -> dict[str, Any]:
    rows = read_rows(LEDGER_CSV)
    if args.only_id:
        rows = [row for row in rows if row.get("资料编号", "") == args.only_id]
    if args.limit:
        rows = rows[: args.limit]

    if args.clean and SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    browser_manager = None
    browser = None
    context = None
    page = None
    need_browser = any(
        (choose_local_files(row)[0] and choose_local_files(row)[0].suffix.lower() in HTML_SUFFIXES)
        for row in rows
    )

    if need_browser and not args.no_browser:
        browser_manager, browser = launch_browser(args.browser_channel, args.browser_executable)
        context = browser.new_context(device_scale_factor=args.device_scale_factor)
        if not args.allow_external_assets:
            context.route("http://*/*", lambda route: route.abort())
            context.route("https://*/*", lambda route: route.abort())
        page = context.new_page()

    entries: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    try:
        for index, row in enumerate(rows, start=1):
            raw_path, text_path = choose_local_files(row)
            viewer_path = viewer_path_for(row)
            image_path = image_path_for(row)
            viewer_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.parent.mkdir(parents=True, exist_ok=True)

            method = "text_fallback"
            status = "ok"
            message = ""
            suffix = raw_path.suffix.lower() if raw_path else ""

            if image_path.exists() and not args.force:
                method = "existing_image"
            else:
                try:
                    if raw_path and suffix in HTML_SUFFIXES and page is not None:
                        render_html_screenshot(page, raw_path, image_path, args)
                        method = "browser_html_full_page" if args.full_page else "browser_html_viewport"
                    elif raw_path and suffix in PDF_SUFFIXES:
                        render_pdf_screenshot(raw_path, image_path, args)
                        method = "pdf_render"
                    else:
                        note = "使用处理后文本生成截图"
                        if raw_path:
                            note = f"原始文件类型 {suffix or '未知'} 暂不支持，使用处理后文本生成截图"
                        render_text_screenshot(row, text_path, image_path, generated_at, args, note=note)
                        method = "text_fallback"
                        message = note
                except Exception as exc:
                    status = "fallback"
                    message = f"原始文件截图失败，已改用处理后文本：{exc}"
                    render_text_screenshot(row, text_path, image_path, generated_at, args, note=message)
                    method = "text_fallback_after_error"

            viewer_html = build_viewer_html(row, raw_path, text_path, image_path, viewer_path, generated_at, method, message)
            viewer_path.write_text(viewer_html, encoding="utf-8", newline="\n")

            stats[method] = stats.get(method, 0) + 1
            entries.append(
                {
                    "doc_id": row.get("资料编号", ""),
                    "title": row.get("文件标题", ""),
                    "original_url": row.get("原文链接", ""),
                    "snapshot_path": root_relative(viewer_path),
                    "snapshot_image_path": root_relative(image_path),
                    "source_path": root_relative(raw_path),
                    "text_path": root_relative(text_path),
                    "method": method,
                    "status": status,
                    "message": message,
                }
            )
            if args.progress_every and (index == 1 or index % args.progress_every == 0 or index == len(rows)):
                print(f"[{index}/{len(rows)}] {row.get('资料编号', '')} {method}", flush=True)
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if browser_manager is not None:
            browser_manager.stop()

    manifest = {
        "mode": "screenshot",
        "generated_at": generated_at,
        "count": len(entries),
        "stats": stats,
        "entries": entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return manifest


def self_check(manifest: dict[str, Any]) -> None:
    entries = manifest.get("entries", [])
    if not entries:
        raise AssertionError("未生成任何网页快照")
    for entry in entries:
        viewer_path = ROOT / entry["snapshot_path"]
        image_path = ROOT / entry["snapshot_image_path"]
        if not viewer_path.exists():
            raise AssertionError(f"快照查看页缺失：{viewer_path}")
        if not image_path.exists() or image_path.stat().st_size <= 0:
            raise AssertionError(f"截图图片缺失或为空：{image_path}")
    sample_html = (ROOT / entries[0]["snapshot_path"]).read_text(encoding="utf-8", errors="ignore")
    if "截图式网页快照" not in sample_html or "snapshot-image" not in sample_html:
        raise AssertionError("快照查看页没有指向截图图片")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成截图式网页快照")
    parser.add_argument("--clean", action="store_true", help="生成前清空既有网页快照目录")
    parser.add_argument("--force", action="store_true", help="覆盖既有截图图片")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，便于试跑")
    parser.add_argument("--only-id", default="", help="只处理指定资料编号")
    parser.add_argument("--no-browser", action="store_true", help="不使用浏览器，HTML 也转为文本截图")
    parser.add_argument("--browser-channel", default="msedge", help="Playwright 浏览器 channel，默认使用本机 Edge")
    parser.add_argument("--browser-executable", default="", help="显式指定浏览器可执行文件路径")
    parser.add_argument("--width", type=int, default=1365, help="截图宽度")
    parser.add_argument("--viewport-height", type=int, default=1800, help="浏览器视口高度")
    parser.add_argument("--device-scale-factor", type=float, default=1.0, help="浏览器缩放倍率")
    parser.add_argument("--quality", type=int, default=82, help="JPEG 质量")
    parser.add_argument("--timeout-ms", type=int, default=15000, help="页面加载和截图超时时间")
    parser.add_argument("--idle-timeout-ms", type=int, default=0, help="等待网络空闲的补充时间")
    parser.add_argument("--render-wait-ms", type=int, default=500, help="页面打开后的固定等待时间")
    parser.add_argument("--allow-external-assets", action="store_true", help="允许本地 HTML 继续加载外部图片/CSS/脚本")
    parser.add_argument("--full-page", action=argparse.BooleanOptionalAction, default=True, help="是否截取完整网页")
    parser.add_argument("--pdf-pages", type=int, default=1, help="PDF 渲染前 N 页")
    parser.add_argument("--pdf-zoom", type=float, default=1.8, help="PDF 渲染清晰度")
    parser.add_argument("--text-limit", type=int, default=5200, help="文本截图最多读取字符数")
    parser.add_argument("--text-max-lines", type=int, default=130, help="文本截图最多绘制行数")
    parser.add_argument("--progress-every", type=int, default=10, help="每处理 N 条输出一次进度")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_snapshots(args)
    self_check(manifest)
    print(f"已生成截图式网页快照：{manifest['count']}")
    print(f"截图目录：{IMAGE_DIR}")
    print(f"快照查看页目录：{SNAPSHOT_DIR}")
    print(f"快照清单：{MANIFEST_PATH}")
    print(f"生成方式统计：{manifest['stats']}")
    print("自检通过")


if __name__ == "__main__":
    main()
