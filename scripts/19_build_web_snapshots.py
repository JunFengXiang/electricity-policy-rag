"""为政策资料生成本地网页快照入口。

快照页不是重新联网抓取，而是把已经保存到 01_原始资料 的 HTML/PDF 包装成
统一的本地查看页。这样官网链接失效时，搜索页仍能打开本地留存证据。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
SNAPSHOT_DIR = OUTPUT_DIR / "网页快照"
MANIFEST_PATH = SNAPSHOT_DIR / "snapshot_manifest.json"


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
        if candidate.suffix.lower() == ".txt" and text_path is None:
            text_path = candidate
        elif raw_path is None:
            raw_path = candidate
    return raw_path, text_path


def relative_href(from_dir: Path, target: Path) -> str:
    rel = target.relative_to(from_dir) if target.is_relative_to(from_dir) else target
    if target.is_absolute():
        try:
            rel = target.relative_to(from_dir)
        except ValueError:
            rel = Path("..") / ".." / target.relative_to(ROOT)
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


def snapshot_filename(row: dict[str, str]) -> str:
    doc_id = row.get("资料编号", "").strip()
    if doc_id:
        return f"{safe_filename(doc_id, 'snapshot')}.html"
    seed = row.get("原文链接", "") or row.get("文件标题", "")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"SNAPSHOT-{digest}.html"


def file_preview_html(raw_path: Path | None, text_path: Path | None, snapshot_path: Path, text_limit: int) -> str:
    if raw_path and raw_path.suffix.lower() in {".html", ".htm", ".pdf"}:
        src = relative_href(snapshot_path.parent, raw_path)
        return f'<iframe class="snapshot-frame" src="{html.escape(src)}" sandbox></iframe>'

    text = read_text_preview(text_path, text_limit)
    if text:
        return f'<pre class="text-preview">{html.escape(text)}</pre>'

    return '<div class="empty">未找到可预览的本地原始文件或处理后文本。</div>'


def build_snapshot_html(
    row: dict[str, str],
    raw_path: Path | None,
    text_path: Path | None,
    snapshot_path: Path,
    generated_at: str,
    text_limit: int,
) -> str:
    title = row.get("文件标题", "") or "未命名政策"
    raw_href = relative_href(snapshot_path.parent, raw_path) if raw_path else ""
    text_href = relative_href(snapshot_path.parent, text_path) if text_path else ""
    preview = file_preview_html(raw_path, text_path, snapshot_path, text_limit)
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
        ("快照生成时间", generated_at),
    ]
    metadata_html = "\n".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(value or '-')}</dd>" for label, value in metadata
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
    original_url = row.get("原文链接", "")
    original_link = (
        f'<a href="{html.escape(original_url)}" target="_blank" rel="noreferrer">官网原文</a>'
        if original_url
        else '<span class="disabled">官网原文缺失</span>'
    )
    raw_path_text = root_relative(raw_path)
    text_path_text = root_relative(text_path)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - 网页快照</title>
  <style>
    :root {{
      --bg: #f6f7f9;
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
      padding: 14px;
    }}
    .snapshot-frame {{
      width: 100%;
      min-height: calc(100vh - 118px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .text-preview, .empty {{
      margin: 0;
      min-height: calc(100vh - 118px);
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 14px/1.65 "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }}
    .empty {{
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    @media (max-width: 860px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .snapshot-frame, .text-preview, .empty {{ min-height: 70vh; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="hint">本页为本地网页快照入口，用于在官网链接失效时保留采集证据。</div>
  </header>
  <main>
    <aside>
      <div class="actions">
        {original_link}
        {raw_link}
        {text_link}
      </div>
      <dl>
        {metadata_html}
      </dl>
      <div class="path">
        <div>原始文件：{html.escape(raw_path_text or "-")}</div>
        <div>处理后文本：{html.escape(text_path_text or "-")}</div>
      </div>
    </aside>
    <section class="viewer">
      {preview}
    </section>
  </main>
</body>
</html>
"""


def build_snapshots(text_limit: int, clean: bool) -> list[dict[str, str]]:
    rows = read_rows(LEDGER_CSV)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if clean:
        for path in SNAPSHOT_DIR.glob("*.html"):
            path.unlink()

    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest: list[dict[str, str]] = []
    for row in rows:
        filename = snapshot_filename(row)
        snapshot_path = SNAPSHOT_DIR / filename
        raw_path, text_path = choose_local_files(row)
        content = build_snapshot_html(row, raw_path, text_path, snapshot_path, generated_at, text_limit)
        snapshot_path.write_text(content, encoding="utf-8")
        manifest.append(
            {
                "资料编号": row.get("资料编号", ""),
                "文件标题": row.get("文件标题", ""),
                "网页快照路径": str(snapshot_path.relative_to(ROOT)).replace("\\", "/"),
                "本地原始文件": root_relative(raw_path),
                "处理后文本": root_relative(text_path),
                "原文链接": row.get("原文链接", ""),
                "快照生成时间": generated_at,
            }
        )

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def self_check(manifest: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    rows = read_rows(LEDGER_CSV)
    if len(manifest) != len(rows):
        errors.append(f"快照数量不一致：{len(manifest)} / {len(rows)}")
    missing = [row for row in manifest if not (ROOT / row["网页快照路径"]).exists()]
    if missing:
        errors.append(f"存在未生成的快照：{len(missing)}")
    if not MANIFEST_PATH.exists():
        errors.append(f"未生成清单：{MANIFEST_PATH}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local web snapshot wrapper pages for policy documents.")
    parser.add_argument("--text-limit", type=int, default=50000, help="Text preview limit when no raw preview exists.")
    parser.add_argument("--clean", action="store_true", help="Remove old snapshot HTML files before rebuilding.")
    args = parser.parse_args()

    manifest = build_snapshots(text_limit=args.text_limit, clean=args.clean)
    errors = self_check(manifest)
    if errors:
        for error in errors:
            print(f"自检失败：{error}")
        return 1
    print(f"已生成网页快照：{len(manifest)}")
    print(f"快照目录：{SNAPSHOT_DIR}")
    print(f"快照清单：{MANIFEST_PATH}")
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
