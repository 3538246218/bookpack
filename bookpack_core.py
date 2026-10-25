"""Batch TXT/EPUB downloads and ZIP export. Python standard library only."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from bookpack_search import SearchCancelled, SearchCandidates, SearchError, SearchService
from bookpack_sources import SourceCancelled, SourceError
from bookpack_suggest import SuggestionService
from bookpack_platforms import PLATFORM_KEYS
from bookpack_content import seed_checkpoints, read_checkpoint
from bookpack_bulk import merge_cached_extras
from bookpack_lightnovel import verify_epub_title


class BookpackError(Exception):
    pass


class Cancelled(BookpackError):
    pass


class TaskCancellation:
    """Stop one selection without cancelling sibling downloads."""
    def __init__(self, batch_event, task_event):
        self.batch_event, self.task_event = batch_event, task_event

    def is_set(self):
        return self.batch_event.is_set() or self.task_event.is_set()

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.task_event.wait(min(.1, remaining))
        return True


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip().rstrip(". ")
    name = name[:110].rstrip(". ") or "未命名"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return name


def parse_tasks(value) -> list[dict]:
    """Read JSON, CSV/TSV, title | URL, or one URL per line."""
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("books", value.get("tasks", []))
    else:
        text = str(value or "").strip().lstrip("\ufeff")
        if not text:
            raise BookpackError("先粘贴书名和下载链接，或导入清单文件。")
        if text.startswith(("[", "{")):
            try:
                return parse_tasks(json.loads(text))
            except json.JSONDecodeError as exc:
                raise BookpackError(f"JSON 格式有误：第 {exc.lineno} 行。") from exc
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        rows = []
        for number, line in enumerate(lines, 1):
            if "|" in line:
                parts = [part.strip() for part in line.split("|", 2)]
            elif "\t" in line:
                parts = [part.strip() for part in line.split("\t", 2)]
            elif line.startswith(("http://", "https://")):
                parts = ["", line]
            else:
                parts = next(csv.reader([line]))
                parts = [part.strip() for part in parts]
            if len(parts) >= 2 and parts[1].lower() in {"url", "download_url", "链接", "下载链接"}:
                continue
            if len(parts) < 2:
                raise BookpackError(f"第 {number} 行缺少链接，格式为：书名 | https://…/book.txt")
            rows.append({"title": parts[0], "url": parts[1], "sha256": parts[2] if len(parts) > 2 else ""})
    if not isinstance(rows, list) or not rows:
        raise BookpackError("清单中没有可下载的任务。")
    result, seen = [], set()
    for number, row in enumerate(rows, 1):
        if isinstance(row, str):
            row = {"url": row}
        if not isinstance(row, dict):
            raise BookpackError(f"第 {number} 项应包含 title 和 url。")
        url = str(row.get("url") or row.get("download_url") or "").strip()
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError()
            parsed.port
        except ValueError as exc:
            raise BookpackError(f"第 {number} 项的下载链接不是有效的 HTTP/HTTPS 地址。") from exc
        title = str(row.get("title") or row.get("name") or "").strip()
        if not title:
            title = Path(urllib.parse.unquote(parsed.path)).stem or f"书籍 {number}"
        expected = str(row.get("sha256") or "").lower().strip()
        if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BookpackError(f"《{title}》的 SHA-256 应为 64 位十六进制字符。")
        key = (title, url, expected)
        if key in seen:
            continue
        seen.add(key)
        result.append({"title": title, "url": url, "expected_sha256": expected})
    return result


def decode_text(data: bytes, declared="") -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    elif data.startswith(b"\xef\xbb\xbf"):
        encodings = ["utf-8-sig"]
    else:
        encodings = ["utf-8"]
        if declared and declared.lower() not in {"iso-8859-1", "latin-1", "ascii"}:
            encodings.append(declared)
        encodings += ["gb18030", "big5"]
    for encoding in dict.fromkeys(encodings):
        try:
            text = data.decode(encoding, errors="strict")
        except (UnicodeError, LookupError):
            continue
        if "\x00" in text:
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff"), encoding
    raise BookpackError("无法识别文本编码；此链接可能返回了二进制文件。")


def check_file(path: Path, content_type="", declared="") -> dict:
    """Check file format, not literary/chapter completeness."""
    if not path.stat().st_size:
        raise BookpackError("服务器返回了空文件。")
    with path.open("rb") as handle:
        head = handle.read(4096)
    if "application/json" in content_type.lower():
        raise BookpackError("该链接返回了 JSON 数据，请使用 TXT／EPUB 文件的直接下载链接。")
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as epub:
                names = set(epub.namelist())
                if "mimetype" not in names or epub.read("mimetype").strip() != b"application/epub+zip":
                    raise BookpackError("下载到的是 ZIP 压缩包，不是 EPUB 书籍。")
                if "META-INF/container.xml" not in names:
                    raise BookpackError("EPUB 缺少 META-INF/container.xml。")
                bad_member = epub.testzip()
                if bad_member:
                    raise BookpackError(f"EPUB 内文件损坏：{bad_member}")
                return {"format": "epub", "encoding": "", "characters": None}
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise BookpackError(f"EPUB 文件无法读取：{exc}") from exc
    if head.startswith((b"%PDF", b"Rar!", b"7z\xbc\xaf", b"\x89PNG")):
        raise BookpackError("当前支持 TXT 和 EPUB；此链接返回了其他格式。")
    raw = path.read_bytes()
    text, encoding = decode_text(raw, declared)
    beginning = text.lstrip()[:4096].lower()
    if "text/html" in content_type.lower() or re.search(r"<!doctype\s+html|<html\b|<head\b|<body\b", beginning):
        if any(word in beginning for word in ["captcha", "cloudflare", "验证码", "人机验证", "login", "登录"]):
            raise BookpackError("该链接要求登录或验证，没有返回全文文件。")
        raise BookpackError("该链接返回了网页，请使用 TXT／EPUB 文件的直接下载链接。")
    if not text.strip():
        raise BookpackError("下载文件中没有文本内容。")
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"format": "txt", "encoding": encoding, "characters": len(text)}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def report_url(url: str) -> str:
    value = urllib.parse.urlsplit(url)
    hostname = value.hostname or ""
    if value.port:
        hostname += f":{value.port}"
    return urllib.parse.urlunsplit((value.scheme, hostname, value.path, "", ""))


class Engine:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.output = self.home / "书包输出"
        self.output.mkdir(parents=True, exist_ok=True)
        self.state_file = self.home / "任务记录.json"
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.runner: threading.Thread | None = None
        self.active_batch_id = None
        self.task_cancellations = {}
        self.search = SearchService(self.home)
        self.suggestions = SuggestionService(self.search)
        self.batches = []
        if self.state_file.exists():
            try:
                self.batches = json.loads(self.state_file.read_text(encoding="utf-8"))["batches"]
            except (ValueError, KeyError, TypeError) as exc:
                raise BookpackError("任务记录无法读取，请保留文件并检查 任务记录.json。") from exc
            for batch in self.batches:
                if batch["status"] in {"running", "stopping", "packing"}:
                    batch["status"] = "interrupted"
                    for task in batch["tasks"]:
                        if task.get("pending_candidate"):
                            self._apply_choice(task, task["pending_candidate"])
                        if task["status"] in {"queued", "searching", "downloading", "checking"}:
                            task["status"] = "cancelled"
                            task["error"] = "上次运行中断，可点击继续未完成任务。"
        self._save()

    def _save(self):
        with self.lock:
            temporary = self.state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": 1, "batches": self.batches}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.state_file)

    def snapshot(self, compact=False):
        with self.lock:
            batches = self.batches
            if compact:
                internal = {"chapters", "source_pages", "web_source", "request_headers", "content_repair", "chapter_repairs",
                            "bulk_downloads", "bulk_pages", "bulk_attempts", "toc_pages", "toc_first_url", "recent_chapters"}
                batches = [{**batch, "tasks": [{key: value for key, value in task.items() if key not in internal}
                                               for task in batch["tasks"]]} for batch in batches]
            result = json.loads(json.dumps({"batches": batches, "output_dir": str(self.output)}, ensure_ascii=False))
        for batch in result["batches"]:
            batch["tasks"] = [self.search.platforms.annotate(task) for task in batch["tasks"]]
            for task in batch["tasks"]:
                for key in ("candidates", "source_matches"):
                    if key in task:
                        task[key] = [self.search.platforms.annotate(row) for row in task[key]]
        return result

    def suggest(self, query, online=False, client_id="", generation=0):
        with self.lock:
            recent = []
            for batch in self.batches:
                for task in batch["tasks"]:
                    if task.get("matched_title"):
                        recent.append({"matched_title": task["matched_title"], "matched_author": task.get("matched_author", ""),
                                       "source": task.get("source") or "已检索书籍"})
                    recent.extend({key: candidate.get(key, "") for key in ("matched_title", "matched_author", "source")}
                                  for candidate in task.get("candidates", []))
        return self.suggestions.lookup(query, recent, online, client_id, generation)

    def _batch(self, batch_id):
        for batch in self.batches:
            if batch["id"] == batch_id:
                return batch
        raise BookpackError("找不到这批任务。")

    def _update(self, task, **changes):
        with self.lock:
            task.update(changes)

    def create(self, tasks: list[dict], name="", concurrency=3):
        with self.lock:
            if self.runner and self.runner.is_alive():
                raise BookpackError("当前还有任务在运行，停止后可创建下一批。")
            batch_id = uuid.uuid4().hex[:12]
            batch_name = safe_name(name or f"书包 {datetime.now():%m月%d日 %H时%M分}")
            directory = f"{datetime.now():%Y%m%d-%H%M%S}-{batch_id}"
            batch = {"id": batch_id, "name": batch_name, "directory": directory,
                     "created_at": now(), "finished_at": None, "status": "running",
                     "concurrency": max(1, min(int(concurrency), 6)), "archive": None,
                     "archive_error": "", "tasks": []}
            for index, item in enumerate(tasks, 1):
                batch["tasks"].append({**item, "id": uuid.uuid4().hex[:12], "index": index,
                    "status": "queued", "bytes": 0, "total": None, "attempt": 0,
                    "error": "", "filename": "", "sha256": "", "source_sha256": "",
                    "format": "", "encoding": "", "characters": None,
                    "started_at": None, "finished_at": None})
            self.batches.insert(0, batch)
            self._save()
            self._start(batch)
            return batch_id

    def _start(self, batch):
        self.cancel_event = threading.Event()
        self.active_batch_id = batch["id"]
        batch["status"] = "running"
        batch["archive_error"] = ""
        batch["finished_at"] = None
        # The old ZIP remains valid until a new one replaces it.
        self.runner = threading.Thread(target=self._run, args=(batch, self.cancel_event), daemon=True)
        self.runner.start()

    def cancel(self, batch_id):
        with self.lock:
            batch = self._batch(batch_id)
            if batch["status"] == "running":
                batch["status"] = "stopping"
                self.cancel_event.set()
                self._save()

    def retry(self, batch_id):
        with self.lock:
            if self.runner and self.runner.is_alive():
                raise BookpackError("上一批仍在收尾，请稍后重试。")
            batch = self._batch(batch_id)
            books = self.output / batch["directory"] / "书籍"
            for task in batch["tasks"]:
                valid = task["status"] == "done" and task["filename"] and not task.get("content_issues")
                if valid:
                    path = books / task["filename"]
                    valid = path.is_file() and file_hash(path) == task["sha256"]
                if not valid:
                    task["bulk_attempts"] = []
                    if task.get("content_issues") and task.get("download_kind") == "web_chapters" and task.get("filename"):
                        path = books / task["filename"]
                        if path.is_file():
                            backup = books.parent / "修复前备份"
                            backup.mkdir(exist_ok=True)
                            if not (backup / path.name).exists():
                                shutil.copy2(path, backup / path.name)
                            for archive in {batch.get("archive"), safe_name(batch["name"]) + ".zip"} - {None}:
                                if (books.parent / archive).is_file() and not (backup / archive).exists():
                                    shutil.copy2(books.parent / archive, backup / archive)
                            seed_checkpoints(task, path.read_text(encoding="utf-8"),
                                             books.parent / "章节缓存" / task["id"])
                        if any(row.get("kind") == "wrong_content" for row in task["content_issues"]):
                            rejected = list(dict.fromkeys(task.get("rejected_source_ids", []) + [task.get("source_id")]))
                            task.update(search_required=True, lookup_title=task.get("matched_title") or task["title"],
                                        lookup_author=task.get("matched_author") or task.get("author", ""),
                                        rejected_source_ids=[value for value in rejected if value],
                                        url="", local_path="", source="", source_id="", source_page="",
                                        chapters=[], source_pages=[], selected_candidate=None, selected_candidate_id=None,
                                        download_kind="", expected_sha256="", request_headers={})
                        task.update(status="queued", bytes=0, total=None, error="", attempt=0, finished_at=None,
                                    chapter_count=0, page_count=0)
                        continue
                    task.update(status="queued", bytes=0, total=None, error="", attempt=0, finished_at=None)
                    if task.get("search_required"):
                        if task.get("selected_candidate"):
                            self._apply_choice(task, task["selected_candidate"])
                            continue
                        task.update(url="", local_path="", source="", source_id="", source_page="", matched_title="", matched_author="", expected_sha256="", download_kind="", page_count=0, source_pages=[], chapter_count=0, chapters=[], source_attempts=[], source_failures=[], source_matches=[], request_headers={}, candidates=[], selected_candidate=None, selected_candidate_id=None, pending_candidate=None, lookup_title="", lookup_author="", match_score=None, match_kind="", match_confidence="", matched_on="")
            self._save()
            self.search.sources.retry_sources()
            self._start(batch)

    def choose(self, batch_id, task_id, candidate_id):
        with self.lock:
            batch = self._batch(batch_id)
            running = self.runner and self.runner.is_alive()
            if running and (self.active_batch_id != batch_id or batch["status"] != "running"):
                raise BookpackError("当前任务正在停止或打包，请完成后改选。")
            task = next((item for item in batch["tasks"] if item["id"] == task_id), None)
            if not task or not task.get("candidates"):
                raise BookpackError("此任务没有保留的候选，请重新搜索。")
            candidate = next((item for item in task.get("candidates", [])
                              if item["candidate_id"] == candidate_id), None)
            if not candidate:
                raise BookpackError("该候选已失效，请重新搜索。")
            if candidate.get("download_kind") == "online_only":
                raise BookpackError("此候选提供官方阅读入口，未提供全文文件。")
            if candidate_id == task.get("selected_candidate_id") and task["status"] in {"queued", "searching", "downloading", "checking", "done"}:
                return
            batch["archive"] = None
            stop = self.task_cancellations.get(task_id)
            if running and stop:
                task["pending_candidate"] = candidate
                stop.set()
            else:
                self._apply_choice(task, candidate)
            self._save()
            if not running:
                self._start(batch)

    def _apply_choice(self, task, candidate):
        # Called only after this task's old worker has stopped writing to it.
        task.update(selected_candidate=candidate, selected_candidate_id=candidate["candidate_id"],
                    pending_candidate=None, status="queued", error="", started_at=None, finished_at=None,
                    lookup_title=candidate["matched_title"],
                    lookup_author=candidate.get("matched_author") or task.get("author", ""),
                    url="", local_path="", source="", source_id="", source_page="", download_kind="",
                    matched_title="", matched_author="", expected_sha256="", request_headers={},
                    bytes=0, total=None, attempt=0, filename="", sha256="", source_sha256="",
                    format="", encoding="", characters=None, chapter_count=0, expected_chapters=0,
                    page_count=0, chapters=[], source_pages=[], source_attempts=[], source_failures=[], toc_pages=[], toc_first_url="", recent_chapters=[],
                    source_matches=[], match_score=None, match_kind="", match_confidence="", matched_on="",
                    content_issues=[], chapter_repairs=[], resumed_chapters=0,
                    bulk_chapters=0, bulk_added_chapters=0, bulk_attempts=[], acquisition_method="")
        task.update(lightnovel_id="", lightnovel_aliases=[], edition_note="", source_updated_at="", work_updated_at="")
        for key in (*PLATFORM_KEYS, "platform_record", "wiki_title", "web_source"):
            task.pop(key, None)
        for key in ("bulk_downloads", "bulk_pages"):
            task.pop(key, None)

    def _process_task(self, task, directory, cancelled, stop):
        try:
            while True:
                with self.lock:
                    pending = task.get("pending_candidate")
                    if pending:
                        self._apply_choice(task, pending)
                    stop.clear()
                self._download(task, directory, TaskCancellation(cancelled, stop))
                with self.lock:
                    if task.get("pending_candidate") and not cancelled.is_set():
                        continue
                    if task.get("pending_candidate"):
                        self._apply_choice(task, task["pending_candidate"])
                        task.update(status="cancelled", error="任务已停止。", finished_at=now())
                    self.task_cancellations.pop(task["id"], None)
                    break
        finally:
            with self.lock:
                self.task_cancellations.pop(task["id"], None)

    def _reuse_chapter_dirs(self, task):
        normalize = self.search.matcher.normalize
        identity = (task.get("source_id"), normalize(task.get("matched_title", "")), normalize(task.get("matched_author", "")))
        result = []
        with self.lock:
            for batch in self.batches:
                for other in batch["tasks"]:
                    if (other["id"] == task["id"] or other["status"] != "done" or other.get("content_issues")
                            or other.get("content_export_version", 0) < 2):
                        continue
                    previous = (other.get("source_id"), normalize(other.get("matched_title", "")), normalize(other.get("matched_author", "")))
                    if previous == identity:
                        cache = self.output / batch["directory"] / "章节缓存" / other["id"]
                        if cache.is_dir():
                            result.append(cache)
                            if task.get("web_source", {}).get("bulk_numbered_toc"):
                                expanded = merge_cached_extras(task["chapters"], other.get("chapters", []), normalize)
                                existing = {row["url"] for row in task["chapters"]}
                                retained = [row for row in expanded if row["url"] in existing or read_checkpoint(cache, row)]
                                task["chapters"], task["expected_chapters"] = retained, len(retained)
        return result

    def _download(self, task, directory: Path, cancelled: threading.Event, excluded_sources=None):
        excluded_sources = set(excluded_sources or ()) | set(task.get("rejected_source_ids", []))
        if cancelled.is_set():
            self._update(task, status="cancelled", error="任务已停止。", finished_at=now())
            return
        if task.get("search_required") and not task.get("url") and not task.get("local_path"):
            self._update(task, status="searching", started_at=task["started_at"] or now(), error="")
            try:
                selected = task.get("selected_candidate")
                if selected:
                    found = self.search.resolve_candidate(selected, task.get("lookup_author", ""), cancelled)
                else:
                    found = self.search.resolve(task.get("lookup_title") or task["title"],
                        task.get("lookup_author") or task.get("author", ""), cancelled, excluded_sources)
                if task.get("lookup_title") and task.get("match_kind") == "fuzzy" and not selected:
                    found.update({key: task[key] for key in
                        ("match_score", "match_kind", "matched_on", "edit_distance", "match_confidence") if key in task})
                self.search.platforms.observe([found])
                found = self.search.platforms.annotate(found)
                # Keep the original ranked works through manual selection and source fallback.
                retained = task.get("candidates") if task.get("lookup_title") else None
                retained = retained or found.get("candidates") or task.get("candidates", [])
                self.search.platforms.observe(retained)
                current = (selected or {}).get("candidate_id") or task.get("selected_candidate_id") or found.get("selected_candidate_id")
                self._update(task, bulk_downloads=[], bulk_pages=[], bulk_attempts=[], bulk_chapters=0, bulk_added_chapters=0,
                             acquisition_method="", lightnovel_id="", lightnovel_aliases=[], edition_note="",
                             source_updated_at="", work_updated_at="", toc_pages=[], toc_first_url="", recent_chapters=[])
                self._update(task, **{key: value for key, value in found.items() if key not in {"title", "author", "aliases", "candidates", "selected_candidate_id"}})
                self._update(task, selected_candidate=None, selected_candidate_id=current,
                             candidates=[self.search.platforms.annotate(row) for row in retained], lookup_title=found["matched_title"],
                             lookup_author=found.get("matched_author") or task.get("author", ""))
                self._save()
            except SearchCancelled as exc:
                self._update(task, status="cancelled", error=str(exc), finished_at=now())
                return
            except SearchCandidates as exc:
                self.search.platforms.observe(exc.candidates)
                self._update(task, status="needs_choice", error=str(exc),
                             candidates=[self.search.platforms.annotate(row) for row in exc.candidates],
                             source_attempts=exc.source_attempts, source_matches=exc.source_matches,
                             finished_at=now())
                self._save()
                return
            except Exception as exc:
                self._update(task, status="failed", error=str(exc), source_attempts=getattr(exc, "source_attempts", []),
                             source_matches=getattr(exc, "source_matches", []), finished_at=now())
                self._save()
                return
        temporary = directory / (task["id"] + ".part")
        books = directory / "书籍"
        books.mkdir(exist_ok=True)
        for attempt in range(1, 4):
            if cancelled.is_set():
                self._update(task, status="cancelled", error="任务已停止。", finished_at=now())
                return
            self._update(task, status="downloading", bytes=0, total=None,
                         error="", attempt=attempt, started_at=task["started_at"] or now())
            try:
                if task.get("download_kind") == "web_chapters":
                    cache = directory / "章节缓存" / task["id"]
                    repair = self.search.chapter_repair(task, cancelled)
                    prefetched = self.search.sources.prefetch(task, cancelled,
                        lambda received, chapters, pages: self._update(task, bytes=received, chapter_count=chapters, page_count=pages),
                        cache, repair, workers=6, reuse_dirs=self._reuse_chapter_dirs(task))
                    self._update(task, status="checking")
                    metadata = self.search.sources.export(task, temporary, cancelled,
                        lambda received, chapters, pages: self._update(task, bytes=received),
                        cache_dir=cache, repair=repair)
                    metadata.update(prefetched)
                    self._update(task, **metadata, bytes=temporary.stat().st_size, total=temporary.stat().st_size)
                    content_type, encoding = "text/plain", "utf-8"
                    source_sha = file_hash(temporary)
                elif task.get("download_kind") == "wikisource":
                    metadata = self.search.export_wikisource(task, temporary, cancelled,
                        lambda received, pages: self._update(task, bytes=received, page_count=pages))
                    self._update(task, **metadata, bytes=temporary.stat().st_size, total=temporary.stat().st_size)
                    content_type, encoding = "text/plain", "utf-8"
                    source_sha = file_hash(temporary)
                else:
                    source_url = Path(task["local_path"]).as_uri() if task.get("local_path") else task["url"]
                    request = urllib.request.Request(source_url, headers={
                        "User-Agent": "BookPack/1.1 (local file downloader)",
                        "Accept": "text/plain,application/epub+zip,application/octet-stream;q=0.9,*/*;q=0.5",
                        "Accept-Encoding": "identity", **task.get("request_headers", {})})
                    source_digest = hashlib.sha256()
                    with urllib.request.urlopen(request, timeout=15) as response:
                        content_type = response.headers.get("Content-Type", "")
                        encoding = response.headers.get_content_charset() or ""
                        length = response.headers.get("Content-Length")
                        total = int(length) if length and length.isdigit() else None
                        self._update(task, total=total)
                        received = 0
                        with temporary.open("wb") as handle:
                            while True:
                                if cancelled.is_set():
                                    raise Cancelled("任务已停止。")
                                chunk = response.read(64 * 1024)
                                if not chunk:
                                    break
                                handle.write(chunk)
                                source_digest.update(chunk)
                                received += len(chunk)
                                self._update(task, bytes=received)
                        if total is not None and received != total:
                            raise OSError(f"传输未完成：收到 {received} / {total} 字节。")
                    source_sha = source_digest.hexdigest()
                if cancelled.is_set():
                    raise Cancelled("任务已停止。")
                if task["expected_sha256"] and source_sha != task["expected_sha256"]:
                    raise BookpackError("下载文件与清单提供的 SHA-256 不一致。")
                self._update(task, status="checking")
                information = check_file(temporary, content_type, encoding)
                if task.get("lightnovel_id") and information["format"] == "epub":
                    try:
                        verify_epub_title(temporary, task, self.search.matcher.normalize)
                    except ValueError as exc:
                        raise BookpackError(str(exc)) from exc
                filename = f"{task['index']:02d} {safe_name(task.get('matched_title') or task['title'])}.{information['format']}"
                destination = books / filename
                temporary.replace(destination)
                self._update(task, status="done", error="", filename=filename,
                             sha256=file_hash(destination), source_sha256=source_sha,
                             finished_at=now(), **information)
                self._save()
                return
            except (Cancelled, SearchCancelled, SourceCancelled) as exc:
                self._update(task, status="cancelled", error=str(exc), finished_at=now())
                break
            except urllib.error.HTTPError as exc:
                exc.close()
                message = {401: "链接要求登录。", 403: "来源拒绝下载，可能需要登录或验证。",
                           404: "下载链接不存在（404）。", 429: "来源请求过多（429），稍后可重试。"}.get(exc.code, f"服务器返回 HTTP {exc.code}。")
                self._update(task, error=message)
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt == 3:
                    self._update(task, status="failed", finished_at=now())
                    break
            except (BookpackError, SearchError, SourceError) as exc:
                self._update(task, status="failed", error=str(exc), finished_at=now())
                break
            except Exception as exc:
                message = str(exc.reason) if isinstance(exc, urllib.error.URLError) else str(exc)
                self._update(task, error=f"下载失败：{message}")
                if attempt == 3:
                    self._update(task, status="failed", finished_at=now())
                    break
            finally:
                if temporary.exists():
                    temporary.unlink()
            if cancelled.wait(attempt * 1.5):
                self._update(task, status="cancelled", error="任务已停止。", finished_at=now())
                break
        if task["status"] == "failed" and task.get("search_required") and task.get("source_id"):
            failed_source = task["source_id"]
            failures = list(task.get("source_failures", [])) + [{"source": task.get("source", ""), "error": task["error"]}]
            self._update(task, url="", local_path="", source="", source_id="", download_kind="", request_headers={},
                         status="queued", error="", bytes=0, total=None, source_failures=failures,
                         expected_chapters=0, chapter_count=0, page_count=0, chapters=[], source_pages=[],
                         source_page="", matched_title="", matched_author="")
            self._save()
            self._download(task, directory, cancelled, excluded_sources | {failed_source})
            return
        self._save()

    def _run(self, batch, cancelled):
        directory = self.output / batch["directory"]
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with ThreadPoolExecutor(max_workers=batch["concurrency"]) as pool:
                futures = {}
                while True:
                    with self.lock:
                        assigned = set(futures.values())
                        for task in batch["tasks"]:
                            if task["status"] == "queued" and task["id"] not in assigned:
                                stop = threading.Event()
                                self.task_cancellations[task["id"]] = stop
                                future = pool.submit(self._process_task, task, directory, cancelled, stop)
                                futures[future] = task["id"]
                        if not futures:
                            batch["status"] = "packing"
                            break
                    completed, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        future.result()
                        del futures[future]
            self._package(batch)
            with self.lock:
                statuses = [task["status"] for task in batch["tasks"]]
                batch["status"] = ("cancelled" if cancelled.is_set() else "needs_choice" if "needs_choice" in statuses
                                   else "complete" if all(s == "done" for s in statuses) else "partial")
                batch["finished_at"] = now()
        except Exception as exc:
            with self.lock:
                batch["status"] = "error"
                batch["archive_error"] = f"打包未完成：{exc}"
                batch["finished_at"] = now()
        finally:
            self._save()

    def _package(self, batch):
        directory = self.output / batch["directory"]
        books = directory / "书籍"
        done = [task for task in batch["tasks"] if task["status"] == "done"]
        report_tasks = []
        for task in batch["tasks"]:
            work = self.search.platforms.annotate(task)
            report_tasks.append({"title": task["title"], "source_url": report_url(task["url"]),
                **{key: work[key] for key in PLATFORM_KEYS},
                "requested_author": task.get("author", ""), "search_source": task.get("source") or ("未匹配" if task.get("search_required") else "手动链接"),
                "matched_title": task.get("matched_title", ""), "matched_author": task.get("matched_author", ""),
                "source_page": task.get("source_page", ""), "local_path": task.get("local_path", ""),
                "source_pages": task.get("source_pages", []), "page_count": task.get("page_count", 0),
                "source_attempts": task.get("source_attempts", []), "chapter_count": task.get("chapter_count", 0),
                "source_matches": task.get("source_matches", []),
                "content_issues": task.get("content_issues", []),
                "chapter_repairs": task.get("chapter_repairs", []), "resumed_chapters": task.get("resumed_chapters", 0),
                "network_chapters": task.get("network_chapters", 0),
                "bulk_chapters": task.get("bulk_chapters", 0), "bulk_added_chapters": task.get("bulk_added_chapters", 0),
                "bulk_attempts": task.get("bulk_attempts", []),
                "match_score": task.get("match_score"), "match_kind": task.get("match_kind", ""),
                "match_confidence": task.get("match_confidence", ""), "matched_on": task.get("matched_on", ""),
                "selected_candidate_id": task.get("selected_candidate_id"),
                "candidates": [{key: candidate.get(key, "") for key in
                    ("candidate_id", "matched_title", "matched_author", "source", "source_page", "match_score")}
                    for candidate in task.get("candidates", [])],
                "source_failures": task.get("source_failures", []),
                "acquisition_method": task.get("acquisition_method", ""),
                "lightnovel_id": task.get("lightnovel_id", ""), "edition_note": task.get("edition_note", ""),
                "source_updated_at": task.get("source_updated_at", ""), "work_updated_at": task.get("work_updated_at", ""),
                "content_repair": task.get("content_repair", {}),
                "expected_chapters": task.get("expected_chapters", 0),
                "status": task["status"], "error": task["error"], "filename": task["filename"],
                "download_bytes": task["bytes"], "source_sha256": task["source_sha256"],
                "file_sha256": task["sha256"], "format": task["format"],
                "original_encoding": task["encoding"], "characters": task["characters"],
                "text_completeness": "已确认存在正文问题，待修复。" if task.get("content_issues")
                    else "未独立核对官方全书；文件传输与格式校验不证明小说无缺章。"})
        report = {"name": batch["name"], "generated_at": now(), "requested": len(batch["tasks"]),
                  "downloaded": len(done), "failed": sum(t["status"] == "failed" for t in batch["tasks"]),
                  "needs_choice": sum(t["status"] == "needs_choice" for t in batch["tasks"]),
                  "cancelled": sum(t["status"] == "cancelled" for t in batch["tasks"]),
                  "content_issues": sum(len(t.get("content_issues", [])) for t in batch["tasks"]), "books": report_tasks}
        (directory / "下载报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["书名", "作者", "作品平台", "网文分类", "下载书源", "匹配书名", "状态", "文件", "下载字节", "原始编码", "文件SHA256", "失败原因"])
        labels = {"done": "已下载", "failed": "失败", "cancelled": "已停止", "queued": "等待中", "needs_choice": "待选择书籍"}
        for task in batch["tasks"]:
            work = self.search.platforms.annotate(task)
            status = "已生成（缺损待修复）" if task.get("content_issues") else labels.get(task["status"], task["status"])
            reason = task["error"] or "；".join(row["title"] + "：" + row["message"] for row in task.get("content_issues", []))
            writer.writerow([task["title"], task.get("author", ""), work["platform_label"], work["platform_category"], task.get("source") or ("未匹配" if task.get("search_required") else "手动链接"), task.get("matched_title", ""), status, task["filename"], task["bytes"], task["encoding"], task["sha256"], reason])
        (directory / "下载报告.csv").write_text(output.getvalue(), encoding="utf-8-sig", newline="")
        if not done:
            batch["archive"] = None
            return
        suffix = "（含缺页待修复）" if report["content_issues"] else ""
        archive_name = safe_name(batch["name"] + suffix) + ".zip"
        temporary = directory / "书包.zip.part"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for task in done:
                archive.write(books / task["filename"], "书籍/" + task["filename"])
            archive.write(directory / "下载报告.json", "下载报告.json")
            archive.write(directory / "下载报告.csv", "下载报告.csv")
        temporary.replace(directory / archive_name)
        batch["archive"] = archive_name

    def archive_path(self, batch_id):
        with self.lock:
            batch = self._batch(batch_id)
            if not batch.get("archive"):
                raise BookpackError("这批任务还没有可下载的书包。")
            return self.output / batch["directory"] / batch["archive"]

    def batch_directory(self, batch_id):
        with self.lock:
            return self.output / self._batch(batch_id)["directory"]
