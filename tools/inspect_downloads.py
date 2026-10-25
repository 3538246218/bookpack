"""Inspect exported chapters; --apply records confirmed issues without deleting old files."""
import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bookpack_content import inspect_export
from bookpack_core import Engine
from bookpack_search import normalize
from bookpack_sources import Document, SourceManager

parser = argparse.ArgumentParser()
parser.add_argument("--home", type=Path, default=ROOT)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--live-pages", action="store_true")
args = parser.parse_args()
root = args.home.resolve()
try:
    origin = json.loads((root / "运行地址.json").read_text(encoding="utf-8-sig"))["url"]
except (OSError, ValueError, KeyError):
    origin = ""
state = json.loads((root / "任务记录.json").read_text(encoding="utf-8"))
manager = SourceManager(root, normalize)
findings = []
for batch in state["batches"]:
    for task in batch["tasks"]:
        if task["status"] != "done" or not task.get("filename"):
            continue
        file = root / "书包输出" / batch["directory"] / "书籍" / task["filename"]
        if not file.is_file():
            continue
        issues = inspect_export(task, file.read_text(encoding="utf-8"))
        issues.extend(row for row in task.get("content_issues", []) if row.get("kind") == "wrong_content")
        if (args.live_pages and task.get("source_id") == "jywushu"
                and task.get("chapters") and task.get("content_export_version", 0) < 2):
            chapter = task["chapters"][0]
            html, final = manager.fetch(chapter["url"], threading.Event())
            following = manager.next_page(Document(html).root, html, final, task["web_source"])
            if following and following not in task.get("source_pages", []):
                issues.append({"kind":"missing_page", "index":0, **chapter, "next_url":following,
                               "message":"已确认本章后续分页未保存；此来源的其他旧章节也需重新获取分页"})
        findings.append({"batch_id":batch["id"], "task_id":task["id"], "title":task["title"],
                         "file":str(file), "issues":issues, "checked_chapters":len(task.get("chapters", []))})
report = {"checked_at":datetime.now().astimezone().isoformat(timespec="seconds"),
          "files":len(findings), "affected_files":sum(bool(row["issues"]) for row in findings),
          "placeholder_chapters":sum(issue["kind"] == "placeholder" for row in findings for issue in row["issues"]),
          "pagination_samples":sum(issue["kind"] == "missing_page" for row in findings for issue in row["issues"]),
          "findings":findings}
(root / "下载检查.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
lines = ["# 下载结果检查", "", f"检查时间：{report['checked_at']}", "",
         f"检查 {report['files']} 份已生成文件，{report['affected_files']} 份存在已确认的正文问题。", "",
         "| 书名 | 已确认问题 | 查看任务 |", "|---|---|---|"]
for row in findings:
    placeholders = sum(issue["kind"] == "placeholder" for issue in row["issues"])
    pagination = any(issue["kind"] == "missing_page" for issue in row["issues"])
    reasons = ([f"{placeholders} 章含待更新占位内容"] if placeholders else [])
    if pagination:
        reasons.append("首章后续分页漏取；其他旧章节待重新取全")
    reasons.extend(issue["message"] for issue in row["issues"] if issue["kind"] not in {"placeholder", "missing_page"})
    action = f"[打开]({origin}?batch={row['batch_id']})" if origin else ""
    lines.append(f"| {row['title'].replace('|', '／')} | {'；'.join(reasons) or '未发现这些特定问题，未据此认定全书完整'} | {action} |")
lines += ["", "占位提示按实际导出的章节逐项定位；分页问题另用来源当前首章核对。未逐章与官方目录和正文比对。",
          "原 TXT 与旧压缩包保留。含问题的任务可以使用‘修复正文’继续处理；补抓失败不会把占位页重新记为成功。", ""]
(root / "下载检查.md").write_text("\n".join(lines), encoding="utf-8")
if args.apply:
    engine = Engine(root)
    lookup = {row["task_id"]:row for row in findings}
    for batch in engine.batches:
        changed = False
        for task in batch["tasks"]:
            if task["id"] in lookup:
                task["content_issues"] = lookup[task["id"]]["issues"]
                changed |= bool(task["content_issues"])
        if changed:
            batch["status"] = "partial"
            engine._package(batch)
    engine._save()
    engine.suggestions.close()
print(json.dumps({key:report[key] for key in ("files","affected_files","placeholder_chapters","pagination_samples")}, ensure_ascii=False))
