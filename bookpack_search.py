"""Title lookup in local files, user catalogs and Chinese Wikisource."""
from __future__ import annotations

import csv
import ctypes
import json
import os
import re
import threading
import unicodedata
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from bookpack_match import TitleMatcher
from bookpack_platforms import WorkPlatforms
from bookpack_sources import DEFAULT_SOURCES, SourceCancelled, SourceContentError, SourceManager, validate_sources


class SearchError(ValueError):
    pass


class SearchCancelled(SearchError):
    pass


class SearchCandidates(SearchError):
    def __init__(self, message, candidates, attempts=None):
        super().__init__(message)
        self.candidates = candidates
        self.source_attempts = attempts or []
        self.source_matches = [attempt["match"] for attempt in self.source_attempts if attempt.get("match")]


def normalize(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    if os.name == "nt" and value:
        buffer = ctypes.create_unicode_buffer(len(value) * 3 + 1)
        mapper = ctypes.windll.kernel32.LCMapStringW
        mapper.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int]
        if mapper(0x0804, 0x02000000, value, len(value), buffer, len(buffer)):
            value = buffer.value
    return "".join(character for character in value if character.isalnum())


def parse_titles(value):
    if isinstance(value, dict):
        rows = value.get("books", value.get("tasks", []))
    elif isinstance(value, list):
        rows = value
    else:
        text = str(value or "").strip().lstrip("\ufeff")
        if not text:
            raise SearchError("先输入书名，一行一本；也可以导入书单文件。")
        if text.startswith(("[", "{")):
            try:
                return parse_titles(json.loads(text))
            except json.JSONDecodeError as exc:
                raise SearchError(f"JSON 格式有误：第 {exc.lineno} 行。") from exc
        rows, csv_header = [], False
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line or "\t" in line:
                parts = re.split(r"[|\t]", line)
            else:
                parts = next(csv.reader([line]))
                # An ordinary book title may contain a comma. CSV needs a header.
                if not rows and normalize(parts[0]) not in {"title", "书名"}:
                    parts = [line]
                elif rows and not csv_header:
                    parts = [line]
            if normalize(parts[0]) in {"title", "书名"} and len(parts) > 1 and normalize(parts[1]) in {"author", "作者"}:
                csv_header = True
                rows.append(None)
                continue
            if not rows:
                csv_header = False
            rows.append({"title": parts[0], "author": parts[1] if len(parts) > 1 else ""})
        rows = [row for row in rows if row is not None]
    if not isinstance(rows, list) or not rows:
        raise SearchError("书单中没有书名。")
    result, seen = [], set()
    for number, row in enumerate(rows, 1):
        if isinstance(row, str):
            row = {"title": row}
        if not isinstance(row, dict):
            raise SearchError(f"第 {number} 项需要填写书名。")
        title = str(row.get("title") or row.get("name") or "").strip()
        if title.startswith("《") and title.endswith("》"):
            title = title[1:-1].strip()
        if not title or title.startswith(("http://", "https://")):
            raise SearchError(f"第 {number} 项需要填写书名；下载链接请切换到“链接导入”。")
        author = str(row.get("author") or "").strip()
        if author.startswith(("http://", "https://")):
            raise SearchError("当前为书名模式；书名加下载链接请切换到“链接导入”。")
        key = (normalize(title), normalize(author))
        if key not in seen:
            seen.add(key)
            result.append({"title": title, "author": author, "url": "", "expected_sha256": "", "search_required": True})
    return result


def validate_books(value, base=None):
    rows = value.get("books", []) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise SearchError("书源目录需要 JSON 数组，或包含 books 数组的对象。")
    books = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not row.get("title"):
            raise SearchError(f"书源目录第 {index} 项缺少 title。")
        url = str(row.get("url") or row.get("download_url") or "").strip()
        local = str(row.get("local_path") or "").strip()
        if not url and not local:
            raise SearchError(f"书源目录第 {index} 项需要 url 或 local_path。")
        if url:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise SearchError(f"书源目录第 {index} 项的下载链接无效。")
            parsed.port
        if local:
            path = Path(local).expanduser()
            local = str(((base or Path.cwd()) / path).resolve() if not path.is_absolute() else path.resolve())
        aliases = row.get("aliases", [])
        if not isinstance(aliases, list):
            raise SearchError(f"书源目录第 {index} 项的 aliases 应为数组。")
        expected = str(row.get("sha256") or row.get("expected_sha256") or "").strip().lower()
        if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise SearchError(f"书源目录第 {index} 项的 sha256 格式有误。")
        books.append({"title": str(row["title"]).strip(), "author": str(row.get("author") or "").strip(),
                      "aliases": [str(alias) for alias in aliases], "url": url, "local_path": local,
                      "source": str(row.get("source") or "自定义书源目录"), "expected_sha256": expected})
    return books


class WikiText(HTMLParser):
    """Rendered work text and ordered links, excluding navigation furniture."""
    blocks = {"p", "div", "br", "hr", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"}
    voids = {"br", "hr", "meta", "link", "img", "input", "wbr", "source", "area", "base", "embed", "col", "param"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.links = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        skip = (bool(self.stack and self.stack[-1][1]) or tag in {"script", "style"}
                or bool(classes & {"noprint", "ws-noexport", "navbox", "mw-editsection", "mw-empty-elt", "pagenum", "sistersitebox", "licenseContainer", "licensetpl"})
                or attrs.get("id") in {"headerContainer", "footertemplate", "ws-data"})
        if not skip:
            if tag in self.blocks:
                self.parts.append("\n")
            if tag == "a" and attrs.get("href"):
                self.links.append(attrs["href"])
        if tag not in self.voids:
            self.stack.append((tag, skip))

    def handle_endtag(self, tag):
        skip = bool(self.stack and self.stack[-1][1])
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not skip and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", "".join(self.parts))).strip()


class SearchService:
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.file = self.home / "书源设置.json"
        self.lock = threading.RLock()
        self.extra_books = []
        self.matcher = TitleMatcher(normalize)
        self.platforms = WorkPlatforms(self.home, normalize)
        self.sources = SourceManager(self.home, normalize)
        self.wiki_api = "https://zh.wikisource.org/w/api.php"
        directories = [str(self.home / "本地书库")]
        if (self.home.parent / "抓取结果").is_dir():
            directories.append(str(self.home.parent / "抓取结果"))
        self.config = {"local_dirs": directories, "wikisource": True, "books": [], "web_sources": validate_sources(DEFAULT_SOURCES)}
        if self.file.exists():
            try:
                self.config = self._validate(json.loads(self.file.read_text(encoding="utf-8-sig")))
            except (ValueError, OSError, TypeError) as exc:
                raise SearchError(f"书源设置无法读取：{exc}") from exc

    def _validate(self, value):
        directories = value.get("local_dirs", [])
        if not isinstance(directories, list) or not all(isinstance(item, str) for item in directories):
            raise SearchError("本地书库需要一行一个文件夹路径。")
        directories = list(dict.fromkeys(str((self.home / Path(item).expanduser()).resolve()) for item in directories if item.strip()))
        return {"local_dirs": directories, "wikisource": bool(value.get("wikisource", True)),
                "books": validate_books(value.get("books", []), self.home),
                "web_sources": validate_sources(value.get("web_sources", self.config.get("web_sources", DEFAULT_SOURCES)))}

    def settings(self):
        with self.lock:
            return json.loads(json.dumps(self.config, ensure_ascii=False))

    def save_settings(self, value):
        config = self._validate(value)
        with self.lock:
            temporary = self.file.with_suffix(".tmp")
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.file)
            self.config = config
        return self.settings()

    @staticmethod
    def _check(cancelled):
        if cancelled.is_set():
            raise SearchCancelled("任务已停止。")

    def local_entries(self, directories, cancelled):
        entries, errors = [], []
        for directory in directories:
            root = Path(directory)
            if not root.exists():
                continue
            if not root.is_dir():
                errors.append(f"本地书库不是文件夹：{root}")
                continue
            try:
                for path in root.rglob("*"):
                    self._check(cancelled)
                    if path.suffix.lower() not in {".txt", ".epub"} or not path.is_file():
                        continue
                    relative = str(path.relative_to(root))
                    if re.search(r"(?i)(?:_raw|batch\d+_txt|probe_|_seg\d+|_stillbad|\.bak[-.])", relative):
                        continue
                    stem = re.sub(r"^\d{1,3}[ .、_-]+", "", path.stem)
                    stem = re.sub(r"[_（(【\[]?(?:全本|完结|完整版)[）)】\]]?$", "", stem)
                    named_author = re.split(r"\s+[-—]\s+", stem, maxsplit=1)
                    known_author = named_author[1] if len(named_author) == 2 else ""
                    entries.append({"url": "", "local_path": str(path.resolve()), "source": "本地书库",
                                    "matched_title": named_author[0], "matched_author": known_author, "source_page": ""})
            except OSError as exc:
                errors.append(f"本地书库读取失败：{exc}")
        return entries, errors

    def _local(self, title, author, directories, cancelled, suggestions):
        entries, errors = self.local_entries(directories, cancelled)
        candidates = []
        for entry in entries:
            self._check(cancelled)
            if author and normalize(entry["matched_author"]) != normalize(author):
                continue
            match = self.matcher.candidate(title, entry["matched_title"])
            if not match:
                continue
            candidate = {**entry, **match}
            if match["match_kind"] == "exact":
                candidates.append((Path(entry["local_path"]), entry["matched_author"], candidate))
            else:
                suggestions.append(candidate)
        if not candidates:
            return None, errors
        known_authors = {normalize(named_author) for _, named_author, _ in candidates if named_author}
        if not author and len(known_authors) > 1:
            raise SearchCandidates("本地书库有不同作者的同名书，请选择作品。",
                                   self.matcher.rank([entry for _, _, entry in candidates]))
        _, _, chosen = sorted(candidates, key=lambda item: (item[0].suffix.lower() == ".epub", item[0].stat().st_mtime), reverse=True)[0]
        return {**chosen, "match_count": len(candidates), "match_confidence": "exact"}, errors

    def _json(self, parameters, timeout=15):
        request = urllib.request.Request(self.wiki_api + "?" + urllib.parse.urlencode(parameters),
            headers={"User-Agent": "BookPack/1.1 (local ebook catalog client)", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if result.get("error"):
            raise SearchError(result["error"].get("info", "维基文库接口出错。"))
        return result

    def _wiki(self, title, author, cancelled):
        self._check(cancelled)
        result = self._json({"action": "query", "format": "json", "titles": title, "redirects": 1,
                             "converttitles": 1, "prop": "info|pageprops", "inprop": "url"})
        self._check(cancelled)
        pages = list(result.get("query", {}).get("pages", {}).values())
        if not pages or "missing" in pages[0] or "invalid" in pages[0]:
            return None
        page = pages[0]
        if "disambiguation" in page.get("pageprops", {}):
            raise SearchError("维基文库有多个同名作品，请补充完整版本名或使用自定义目录。")
        # Exact-title resolution follows MediaWiki redirects and script conversion.
        # An optional author must be verifiable in the work's source metadata.
        if author:
            parsed = self._json({"action": "parse", "format": "json", "pageid": page["pageid"], "prop": "wikitext"})
            source = parsed.get("parse", {}).get("wikitext", {}).get("*", "")
            authors = re.findall(r"\|\s*(?:author|作者)\s*=\s*([^\n|}]+)", source, flags=re.I)
            if not any(normalize(author) == normalize(re.sub(r"\[\[|\]\]|作者:|Author:", "", item)) for item in authors):
                raise SearchError("维基文库找到了书名，但无法核实指定作者；可补充自定义目录中的作者信息。")
        canonical = page["title"]
        if page.get("ns", 0) != 0 or "/" in canonical and "/" not in title:
            return None
        return {"url": page.get("fullurl", ""), "download_kind": "wikisource", "wiki_title": canonical,
                "source": "维基文库", "source_page": page.get("fullurl", ""), "matched_title": canonical,
                "matched_author": author, "local_path": "", "match_score": 1.0,
                "match_kind": "exact", "match_confidence": "exact", "matched_on": title, "edit_distance": 0}

    def _wiki_candidates(self, title, cancelled):
        self._check(cancelled)
        result = self._json({"action": "query", "format": "json", "list": "search",
                             "srsearch": title, "srnamespace": 0, "srlimit": 8})
        self._check(cancelled)
        candidates = []
        for page in result.get("query", {}).get("search", []):
            name = page.get("title", "")
            match = self.matcher.candidate(title, name)
            if not match or "/" in name:
                continue
            url = "https://zh.wikisource.org/wiki/" + urllib.parse.quote(name)
            candidates.append({"url": url, "source_page": url, "source": "维基文库", "local_path": "",
                               "matched_title": name, "matched_author": "", "download_kind": "wiki_candidate", **match})
        return candidates

    def resolve_candidate(self, candidate, author, cancelled, confidence="selected"):
        self._check(cancelled)
        kind = candidate.get("download_kind", "")
        if kind == "catalog_candidate":
            found = self.resolve(candidate["matched_title"], author, cancelled)
        elif kind == "web_candidate":
            try:
                source = candidate["web_source"]
                configured = next((row for row in self.settings()["web_sources"] if row["id"] == source["id"]), None)
                if configured is not None and not configured.get("enabled"):
                    found = self.resolve(candidate["matched_title"], author or candidate.get("matched_author", ""),
                                         cancelled, {source["id"]})
                else:
                    found = self.sources.book(source, candidate["source_page"],
                                              candidate["matched_title"], author, cancelled)
            except SourceCancelled as exc:
                raise SearchCancelled(str(exc)) from exc
            if not found:
                raise SearchError("候选来源的实际书名或作者与所选作品不符，请选择其他候选或重新搜索。")
        elif kind == "wiki_candidate":
            found = self._wiki(candidate["matched_title"], author, cancelled)
            if not found:
                raise SearchError("所选候选当前已无法从维基文库取得，请重新搜索。")
        elif kind == "online_only":
            raise SearchError("此来源提供官方阅读入口，未提供全文文件。")
        else:
            found = candidate
        return {**found, **{key: candidate[key] for key in
                ("match_score", "match_kind", "matched_on", "edit_distance") if key in candidate},
                "match_confidence": confidence}

    def chapter_repair(self, task, cancelled):
        sources = [row for row in self.settings()["web_sources"] if row.get("enabled")
                   and row.get("id") != task.get("source_id") and row.get("kind") not in {"fanqie", "qidian"}]
        books = {}
        book_lock = threading.RLock()

        def heading(value):
            value = re.sub(r"^\d+\.\s*", "", value)
            return normalize(re.sub(r"^第[零〇一二三四五六七八九十百千万两0-9]+[章节回卷]\s*", "", value))

        def repair(chapter, original_error):
            for source in sources:
                self._check(cancelled)
                key = source["id"]
                with book_lock:
                    if key not in books:
                        try:
                            found = self.sources.lookup(source, task["matched_title"], task.get("matched_author", ""), cancelled)
                            if found and found.get("download_kind") == "web_chapters":
                                self.sources.complete_toc(found, cancelled)
                                exact, subtitles = {}, {}
                                for row in found["chapters"]:
                                    exact.setdefault(normalize(row["title"]), []).append(row)
                                    subtitles.setdefault(heading(row["title"]), []).append(row)
                                books[key] = (found, exact, subtitles)
                            else:
                                books[key] = None
                        except SourceCancelled:
                            raise
                        except Exception:
                            books[key] = None
                    indexed = books[key]
                if not indexed:
                    continue
                book, exact, subtitles = indexed
                choices = exact.get(normalize(chapter["title"]), [])
                wanted = heading(chapter["title"])
                if not choices and len(wanted) >= 2:
                    choices = subtitles.get(wanted, [])
                if len(choices) != 1:
                    continue
                try:
                    text, pages = self.sources.chapter(book, choices[0], cancelled)
                    return text, pages, {"title": chapter["title"], "original_url": chapter["url"],
                        "source": source["name"], "source_page": book["source_page"],
                        "replacement_url": choices[0]["url"], "reason": str(original_error)}
                except SourceCancelled:
                    raise
                except Exception:
                    continue
            raise SourceContentError(f"{original_error}；其他来源未找到可核对的同一章节。") from original_error

        return repair

    def _retain_candidates(self, found, suggestions):
        current = {key: value for key, value in found.items() if key not in
                   {"source_attempts", "source_matches", "chapters", "expected_chapters", "source_pages", "page_count"}}
        if current.get("download_kind") == "web_chapters":
            current["download_kind"] = "web_candidate"
        location = current.get("source_page") or current.get("local_path") or current.get("url")
        # A search row without an author and its resolved page are one candidate.
        others = [row for row in suggestions if not (location
                  and location == (row.get("source_page") or row.get("local_path") or row.get("url"))
                  and normalize(row["matched_title"]) == normalize(current["matched_title"])
                  and not row.get("matched_author"))]
        candidates = self.matcher.rank([current, *others])
        identity = (normalize(found["matched_title"]), normalize(found.get("matched_author", "")))
        current = next(row for row in candidates if
                       (normalize(row["matched_title"]), normalize(row.get("matched_author", ""))) == identity)
        return {**found, "candidates": candidates, "selected_candidate_id": current["candidate_id"]}

    def export_wikisource(self, task, destination, cancelled, progress):
        root = task["wiki_title"]
        pending, visited, exported = [root], set(), []
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(root + "\n来源：" + task["source_page"] + "\n\n")
            while pending:
                self._check(cancelled)
                title = pending.pop(0)
                if title in visited:
                    continue
                visited.add(title)
                parsed = self._json({"action": "parse", "format": "json", "page": title, "prop": "text"}).get("parse", {})
                self._check(cancelled)
                reader = WikiText()
                reader.feed(parsed.get("text", {}).get("*", ""))
                reader.close()
                text = reader.text()
                if not text:
                    raise SearchError(f"正文页为空：{title}")
                handle.write("\n\n" + title + "\n\n" + text + "\n")
                handle.flush()
                exported.append(title)
                progress(destination.stat().st_size, len(exported))
                children = []
                for href in reader.links:
                    link = urllib.parse.urlsplit(href)
                    if link.netloc and link.netloc != "zh.wikisource.org":
                        continue
                    if not link.path.startswith("/wiki/"):
                        continue
                    child = urllib.parse.unquote(link.path[len("/wiki/"):]).replace("_", " ")
                    if child.startswith(root + "/") and child not in visited and child not in pending and child not in children:
                        children.append(child)
                pending[:0] = children
            handle.write("\n\n来源页面与修订记录、许可说明见维基文库原页面。\n")
        return {"source_pages": exported, "page_count": len(exported)}

    def resolve(self, title, author, cancelled, excluded_sources=None):
        self._check(cancelled)
        config = self.settings()
        rows = list(self.extra_books) + config["books"]
        suggestions = []
        for row in rows:
            if author and normalize(author) != normalize(row.get("author", "")):
                continue
            match = self.matcher.candidate(title, row["title"], row.get("aliases", []))
            if match:
                suggestions.append({**row, "matched_title": row["title"], "matched_author": row.get("author", ""),
                                    "source_page": row.get("source_page", ""), **match})
        matches = [row for row in rows if normalize(title) in {normalize(row["title"]), *(normalize(alias) for alias in row.get("aliases", []))}
                   and (not author or normalize(author) == normalize(row.get("author", "")))]
        if matches:
            identities = {(normalize(row["title"]), normalize(row.get("author", ""))) for row in matches}
            if not author and len({identity[1] for identity in identities}) > 1:
                raise SearchCandidates("书源目录里有不同作者的同名书，请选择作品。",
                    self.matcher.rank([row for row in suggestions if row["match_kind"] in {"exact", "alias"}]))
            match = matches[0]
            return self._retain_candidates({**match, "matched_title": match["title"], "matched_author": match.get("author", ""),
                    "source_page": match.get("source_page", ""), **self.matcher.match(title, match["title"], match.get("aliases", [])),
                    "match_confidence": "exact"}, suggestions)
        local, errors = self._local(title, author, config["local_dirs"], cancelled, suggestions)
        if local:
            return self._retain_candidates(local, suggestions)
        attempts = []
        preferred_author = author
        if not preferred_author:
            known = [row for row in self.platforms.suggestions(title)
                     if normalize(title) in {normalize(row["title"]), *(normalize(alias) for alias in row.get("aliases", []))}]
            authors = {normalize(row["author"]): row["author"] for row in known if row.get("author")}
            if len(authors) == 1:
                preferred_author = next(iter(authors.values()))
        try:
            found = self.sources.find([source for source in config["web_sources"] if source["id"] not in (excluded_sources or set())],
                                      title, author, cancelled, attempts, suggestions, preferred_author=preferred_author)
        except SourceCancelled as exc:
            raise SearchCancelled(str(exc)) from exc
        if found:
            return self._retain_candidates({**found, "source_attempts": attempts}, suggestions)
        errors.extend(f"{attempt['source']}：{attempt['error']}" for attempt in attempts if attempt["error"])
        if config["wikisource"]:
            try:
                found = self._wiki(title, author, cancelled)
                if found:
                    return self._retain_candidates({**found, "source_attempts": attempts}, suggestions)
                suggestions.extend(self._wiki_candidates(title, cancelled))
            except SearchCancelled:
                raise
            except Exception as exc:
                errors.append(f"维基文库：{exc}")
        self._check(cancelled)
        for row in self.platforms.suggestions(title):
            if author and normalize(author) != normalize(row.get("author", "")):
                continue
            match = self.matcher.candidate(title, row["title"], row.get("aliases", []))
            if not match or match["match_kind"] != "prefix":
                continue
            work = self.platforms.annotate(row)
            suggestions.append({"matched_title": row["title"], "matched_author": row.get("author", ""),
                "source": "作品目录", "download_kind": "catalog_candidate", "url": "", "local_path": "",
                "source_page": work["platform_records"][0]["official_url"], **match})
        candidates = self.matcher.rank(suggestions)
        automatic = self.matcher.automatic(title, candidates, author)
        if automatic:
            try:
                found = self.resolve_candidate(automatic, author, cancelled, confidence="high")
                return self._retain_candidates({**found, "source_attempts": attempts}, candidates)
            except SearchCancelled:
                raise
            except Exception as exc:
                errors.append(f"相近书名：{exc}")
        if candidates:
            raise SearchCandidates("找到这些相近书名，点击即可继续。", candidates, attempts)
        detail = "；".join(errors)
        failure = SearchError("未找到可获取的同名书籍。" + (detail if detail else "可在书源设置中添加本地书库或自有下载目录。"))
        failure.source_attempts = attempts
        failure.source_matches = [attempt["match"] for attempt in attempts if attempt.get("match")]
        raise failure
