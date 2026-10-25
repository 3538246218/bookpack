"""Chapter placeholders, local checkpoints and inspection of existing exports."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path


def chapter_problem(text):
    if not text.strip():
        return "章节正文为空"
    tail = re.sub(r"\s+", "", text[-1400:])
    if (re.search(r"正在手打.{0,80}(?:内容|內容)更新.{0,50}(?:刷新|重新載入)", tail)
            or len(text.strip()) < 500 and re.search(r"正在手打中.{0,40}(?:稍等片刻|重新刷新)", tail)):
        return "来源返回正在手打、等待刷新的占位页"
    if len(text.strip()) < 1200 and re.search(r"(?:章节不存在|章節不存在|本章内容正在更新|本章內容正在更新|章节加载失败|章節載入失敗)", tail):
        return "来源返回缺章或加载失败提示"
    return ""


def checkpoint_file(directory, url):
    return Path(directory) / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")


@lru_cache(maxsize=16)
def _bulk_index(path, modified, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_bulk_index(directory):
    path = Path(directory) / "整本索引.json"
    try:
        stat = path.stat()
        index = _bulk_index(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        return index if isinstance(index, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def read_checkpoint(directory, chapter):
    if directory is None:
        return None
    def valid(row):
        return (isinstance(row, dict) and row.get("url") == chapter["url"] and row.get("title") == chapter["title"]
                and row.get("complete") is True and row.get("pages")
                and not chapter_problem(row.get("text", "")))
    try:
        row = json.loads(checkpoint_file(directory, chapter["url"]).read_text(encoding="utf-8"))
        if valid(row):
            return row
    except (OSError, ValueError, TypeError):
        pass
    try:
        name, offset, length = read_bulk_index(directory)[chapter["url"]][:3]
        with (Path(directory) / name).open("rb") as handle:
            handle.seek(offset)
            row = json.loads(handle.read(length))
        if valid(row):
            return row
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def cached_bulk_extras(directory):
    chapters = []
    for url, entry in read_bulk_index(directory).items():
        if isinstance(entry, list) and len(entry) >= 5 and entry[4]:
            chapter = {"url": url, "title": entry[3], "bulk_only": True}
            if read_checkpoint(directory, chapter):
                chapters.append(chapter)
    return chapters


def write_checkpoint(directory, chapter, text, pages, repair=None, create_directory=True):
    row = {**chapter, "text": text, "pages": pages, "complete": True}
    if repair:
        row["repair"] = repair
    if directory is not None:
        if create_directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
        target = checkpoint_file(directory, chapter["url"])
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    return row


def write_bulk_checkpoints(directory, rows):
    """Commit many chapter rows in two file writes; keep a valid prefix if row generation stops."""
    directory = Path(directory)
    target = directory / ("整本正文-" + uuid.uuid4().hex + ".jsonl")
    temporary = target.with_suffix(".tmp")
    index = dict(read_bulk_index(directory))
    written = 0
    try:
        with temporary.open("wb") as handle:
            for row in rows:
                raw = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                offset = handle.tell()
                handle.write(raw)
                index[row["url"]] = [target.name, offset, len(raw), row["title"], bool(row.get("bulk_only"))]
                written += 1
    finally:
        if written:
            temporary.replace(target)
            index_path = directory / "整本索引.json"
            index_temporary = index_path.with_suffix(".tmp")
            index_temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            index_temporary.replace(index_path)
        elif temporary.exists():
            temporary.unlink()
    return written


def exported_sections(task, text):
    positions, cursor = [], 0
    for chapter in task.get("chapters", []):
        marker = "\n\n" + chapter["title"] + "\n\n"
        start = text.find(marker, cursor)
        if start < 0:
            return []
        body = start + len(marker)
        positions.append((start, body, chapter))
        cursor = body
    return [(chapter, text[body:positions[index + 1][0] if index + 1 < len(positions) else len(text)].strip())
            for index, (_, body, chapter) in enumerate(positions)]


def inspect_export(task, text):
    issues = []
    sections = exported_sections(task, text)
    if task.get("chapters") and len(sections) != len(task["chapters"]):
        return [{"kind": "structure", "index": 0, "title": task.get("matched_title") or task.get("title", ""),
                 "message": "无法按已保存目录定位全部章节，不能据此确认导出完整"}]
    for index, (chapter, body) in enumerate(sections):
        problem = chapter_problem(body)
        if problem:
            issues.append({"kind": "placeholder", "index": index, **chapter, "message": problem})
    return issues


def replace_exported(task, text, replacements):
    sections = exported_sections(task, text)
    if len(sections) != len(task.get("chapters", [])) or not sections:
        raise ValueError("无法按原目录定位全部章节，保留原文件。")
    first = "\n\n" + sections[0][0]["title"] + "\n\n"
    header = text[:text.index(first)]
    return header + "".join("\n\n" + chapter["title"] + "\n\n" + replacements.get(index, body) + "\n"
                            for index, (chapter, body) in enumerate(sections))


def seed_checkpoints(task, text, directory):
    """Keep good legacy chapters; exports with confirmed missing pages must be read again."""
    if any(row.get("kind") in {"missing_page", "wrong_content", "structure"} for row in task.get("content_issues", [])):
        return 0
    if len(task.get("source_pages", [])) != len(task.get("chapters", [])):
        return 0
    count = 0
    for index, (chapter, body) in enumerate(exported_sections(task, text)):
        if not chapter_problem(body):
            write_checkpoint(directory, chapter, body, [task["source_pages"][index]])
            count += 1
    return count
