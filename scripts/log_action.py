from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "06_实验日志"
CSV_LOG = LOG_DIR / "操作日志.csv"
MD_LOG = LOG_DIR / "实验日志.md"

LOG_FIELDS = [
    "时间",
    "操作类型",
    "操作内容",
    "涉及文件或目录",
    "运行命令",
    "结果",
    "备注",
]


def now_text() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def ensure_log_files() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_LOG.exists() or CSV_LOG.stat().st_size == 0:
        with CSV_LOG.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            writer.writeheader()
    if not MD_LOG.exists() or MD_LOG.stat().st_size == 0:
        MD_LOG.write_text(
            "# 电力政策知识库实验日志\n\n"
            "本日志用于记录知识库建设过程中的每一步操作。\n",
            encoding="utf-8",
        )


def append_log(
    action_type: str,
    content: str,
    files: str = "",
    command: str = "",
    result: str = "",
    note: str = "",
    timestamp: str | None = None,
) -> None:
    ensure_log_files()
    timestamp = timestamp or now_text()

    row = {
        "时间": timestamp,
        "操作类型": action_type,
        "操作内容": content,
        "涉及文件或目录": files,
        "运行命令": command,
        "结果": result,
        "备注": note,
    }

    with CSV_LOG.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        writer.writerow(row)

    md_entry = [
        "",
        f"### {timestamp} {action_type}",
        "",
        f"- 操作内容：{content}",
    ]
    if files:
        md_entry.append(f"- 涉及文件或目录：`{files}`")
    if command:
        md_entry.extend(["- 运行命令：", "", "```powershell", command, "```"])
    if result:
        md_entry.append(f"- 结果：{result}")
    if note:
        md_entry.append(f"- 备注：{note}")

    with MD_LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(md_entry) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Append an operation log entry.")
    parser.add_argument("--type", required=True, help="Operation type")
    parser.add_argument("--content", required=True, help="Operation content")
    parser.add_argument("--files", default="", help="Related files or directories")
    parser.add_argument("--command", default="", help="Command that was executed")
    parser.add_argument("--result", default="", help="Operation result")
    parser.add_argument("--note", default="", help="Extra note")
    args = parser.parse_args()

    append_log(
        action_type=args.type,
        content=args.content,
        files=args.files,
        command=args.command,
        result=args.result,
        note=args.note,
    )
    print(f"Logged: {args.type} - {args.content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
