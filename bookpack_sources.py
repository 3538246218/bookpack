"""HTTP book-source adapters. No browser or third-party runtime required."""
from __future__ import annotations

import copy
import gzip
import http.cookiejar
import json
import re
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import zlib
from datetime import datetime
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser
from pathlib import Path

from bookpack_match import TitleMatcher
from bookpack_platforms import official_record
from bookpack_content import chapter_problem, read_checkpoint, write_checkpoint, write_bulk_checkpoints, cached_bulk_extras
from bookpack_bulk import match_sections, parse_sections, text_files, merge_cached_extras
from bookpack_lightnovel import CATALOG_URL, LightNovelCatalog
from bookpack_source_rules import EXTRA_SOURCES, quanben_request, quanben_result, result_title

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
DEFAULT_SOURCES = [
    {"id": "wenku8_catalog", "name": "轻小说文库（公开整理）", "url": CATALOG_URL, "enabled": True,
     "kind": "lightnovel_catalog", "capability": "日轻书名、作者和别名检索；公开整本文件，版本以文件日期为准"},
    {"id": "ixdzs", "name": "爱下电子书", "url": "https://ixdzs.tw/", "enabled": True,
     "chapter_selector": ".u-chapter a", "include_all_toc_entries": True,
     "content_selector": "article.page-content", "bulk_numbered_toc": True},
    {"id": "77shuku", "name": "77读书", "url": "http://www.77shuku.info/", "enabled": True,
     "content_selector": "#ChapterContents, #chaptercontent, #content, .content",
     "bulk_selector": ".button_list a"},
    {"id": "jywushu", "name": "御书网", "url": "https://www.jywushu.com/", "enabled": False,
     "quality_note": "实际下载中发现同一标题混入其他作品正文，默认停用。",
     "search": {"url": "/search.html", "params": {"eb": "{title}"}}, "chapter_selector": ".chapter_list a",
     "content_selector": ".content", "prefer_chapters": True},
    {"id": "qbtr", "name": "全本同人", "url": "https://www.qbtr.org/", "enabled": True,
     "aliases": ["https://qbtr.me/", "https://www.qbtr.cc/"], "encoding": "gb18030",
     "search": {"url": "/e/search/index.php", "method": "POST", "params": {"keyboard": "{title}", "show": "title", "classid": "0"}},
     "chapter_selector": ".book_list a, .clearfix li a", "content_selector": ".read_chapterDetail p"},
    {"id": "qsyz", "name": "奇异网／奇书网", "url": "https://www.qsyz.net/", "enabled": True,
     "search": {"url": "/search/", "method": "POST", "params": {"searchkey": "{title}", "searchtype": "all"}},
     "author_selector": ".novel_info_title i a", "chapter_selector": "ul li a",
     "content_selector": "#article p", "next_page_pattern": "url_next:'([^']+)'"},
    {"id": "ibiquge", "name": "爱笔楼吧", "url": "https://www.ibiquge.info/", "enabled": True,
     "search": {"url": "/search.html?name={title}"},
     "content_selector": "#content, #chaptercontent, .read-content"},
    {"id": "bixiange", "name": "笔仙阁子", "url": "https://209.74.107.162/", "enabled": True,
     "content_selector": "#content, #chaptercontent, .read-content"},
    {"id": "fanqie", "name": "番茄小说（官方检索）", "url": "https://fanqienovel.com/", "enabled": True,
     "kind": "fanqie", "capability": "检索作品和官方阅读入口；不提供全文文件"},
    {"id": "qidian", "name": "起点中文网（官方作品目录）", "url": "https://m.qidian.com/", "enabled": True,
     "kind": "qidian", "capability": "检索作品、作者与作品平台；提供官方阅读入口"},
]
DEFAULT_SOURCES[3:3] = EXTRA_SOURCES


class SourceError(ValueError):
    pass


class SourceCancelled(SourceError):
    pass


class SourceContentError(SourceError):
    pass


class _StopGroup:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.events[0].wait(min(.05, remaining))
        return True


class Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs or []), parent, []

    def all(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.all()

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        if self.tag in {"br", "hr"}:
            return "\n"
        text = "".join(child.text() if isinstance(child, Node) else child for child in self.children)
        if self.tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            text += "\n"
        return text

    def select(self, selector):
        result = []
        for branch in selector.split(","):
            tokens = branch.strip().replace(">", " ").split()
            current = [self]
            for token in tokens:
                current = [node for root in current for node in root.all() if node.matches(token)]
            if tokens:
                result.extend(current)
        return list(dict.fromkeys(result))

    def matches(self, token):
        if not re.fullmatch(r"(?:[\w*-]+)?(?:[.#][\w-]+)*", token):
            raise SourceError(f"当前不支持此选择器：{token}")
        tag = re.match(r"^[\w*-]+", token)
        if tag and tag[0] not in {"*", self.tag}:
            return False
        for marker, value in re.findall(r"([.#])([\w-]+)", token):
            if marker == "#" and self.attrs.get("id") != value:
                return False
            if marker == "." and value not in self.attrs.get("class", "").split():
                return False
        return True


class Document(HTMLParser):
    voids = {"br", "hr", "img", "input", "meta", "link", "wbr", "source", "area", "base", "embed", "col", "param"}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.current = self.root
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        # Deal with common optional closing tags in old novel templates.
        if tag in {"li", "p", "dt", "dd"} and self.current.tag == tag:
            self.current = self.current.parent or self.root
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.voids:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def clean(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\u3000]+", " ", value)).strip()


def validate_sources(rows):
    if not isinstance(rows, list):
        raise SourceError("在线书源应为 JSON 数组。")
    result, seen = [], set()
    for index, value in enumerate(rows, 1):
        if not isinstance(value, dict):
            raise SourceError(f"第 {index} 个书源格式有误。")
        row = copy.deepcopy(value)
        if row.get("bookSourceUrl"):
            row = import_legado(row)
        url = str(row.get("url") or "").strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SourceError(f"第 {index} 个书源需要完整 HTTP／HTTPS 网址。")
        parsed.port
        source_id = str(row.get("id") or parsed.hostname).strip()
        if source_id in seen:
            raise SourceError(f"书源标识重复：{source_id}")
        seen.add(source_id)
        row.update(id=source_id, name=str(row.get("name") or parsed.hostname), url=url, enabled=bool(row.get("enabled", True)))
        for key in ("chapter_selector", "content_selector", "result_selector", "book_title_selector", "author_selector",
                    "toc_link_selector", "toc_page_selector", "next_page_selector", "latest_selector", "latest_exclude_selector",
                    "chapter_group_selector", "chapter_group_title_selector"):
            for branch in str(row.get(key) or "").split(","):
                for token in branch.replace(">", " ").split():
                    Node().matches(token)
        result.append(row)
    return result


def import_legado(row):
    """Import ordinary HTTP + CSS rules. Executable JS rules are not evaluated."""
    def selector(value):
        value = str(value or "").split("##", 1)[0]
        if re.search(r"<js>|@js:|\{\{|\$\.|//", value):
            raise SourceError("这份阅读书源使用 JavaScript／XPath 规则，当前不能直接执行；请提供 HTTP 与 CSS 规则。")
        value = re.sub(r"@(text|textNodes|all|html|href|src)$", "", value)
        value = value.replace("@", " ")
        value = re.sub(r"\bclass\.", ".", value)
        value = re.sub(r"\bid\.", "#", value)
        return re.sub(r"\btag\.", "", value)
    raw_search = str(row.get("searchUrl") or "")
    parts = raw_search.split(",{", 1)
    options = json.loads("{" + parts[1]) if len(parts) == 2 else {}
    path = parts[0].strip().replace("{{key}}", "{title}").replace("{{page}}", "1")
    body = str(options.get("body") or "").replace("{{key}}", "{title}").replace("{{page}}", "1")
    if "{{" in path + body or "@js:" in path + body or "<js>" in path + body:
        raise SourceError("这份阅读书源的搜索地址包含脚本，当前不能直接执行。")
    toc = row.get("ruleToc", {})
    chapter_selector = selector(toc.get("chapterList", ""))
    url_rule = str(toc.get("chapterUrl") or "")
    if url_rule not in {"", "href", "@href"}:
        chapter_selector += " " + selector(url_rule)
    return {"id": row.get("bookSourceUrl"), "name": row.get("bookSourceName", "导入书源"),
            "url": row["bookSourceUrl"], "enabled": row.get("enabled", True),
            "encoding": options.get("charset", "utf-8"),
            "search": {"url": path, "method": options.get("method", "GET"), "params": dict(urllib.parse.parse_qsl(body))},
            "chapter_selector": chapter_selector.strip(),
            "content_selector": selector(row.get("ruleContent", {}).get("content", "")),
            "rule_origin": "阅读 JSON 导入"}


class SourceManager:
    def __init__(self, home, normalize):
        self.home, self.normalize = Path(home), normalize
        self.matcher = TitleMatcher(normalize)
        self.lightnovels = LightNovelCatalog(self.matcher)
        self.lock = threading.RLock()
        self.network_slots = threading.BoundedSemaphore(8)
        self.lookup_cache = {}
        self.toc_cache = {}
        self.search_budget = 10
        self.status_file = self.home / "书源状态.json"
        self.status = {}
        if self.status_file.exists():
            try:
                self.status = json.loads(self.status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def record(self, source, state, message, **details):
        with self.lock:
            self.status[source["id"]] = {"state": state, "message": message,
                "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"), **details}
            temporary = self.status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.status_file)

    def retry_sources(self):
        with self.lock:
            for value in self.status.values():
                value["retry_after"] = 0

    def snapshot(self, sources):
        with self.lock:
            return [{**source, "health": self.status.get(source["id"], {"state": "unknown", "message": "尚未验证"})} for source in sources]

    @staticmethod
    def check(cancelled):
        if cancelled.is_set():
            raise SourceCancelled("任务已停止。")

    def session(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def author_key(self, value):
        return self.normalize(re.sub(r"^[【\[（(](?:清|明|宋|唐|汉|先秦|东晋|晋|元|南北朝|南朝|北朝|隋|民国)[】\]）)]\s*", "", value))

    def fetch(self, url, cancelled, opener=None, data=None, encoding="", referer="", timeout=12):
        self.check(cancelled)
        request = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Referer": referer or url,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Encoding": "gzip, deflate"})
        while not self.network_slots.acquire(timeout=.1):
            self.check(cancelled)
        try:
            self.check(cancelled)
            with (opener or self.session()).open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise SourceError(f"书源返回 HTTP {response.status}，未取得正常页面。")
                raw = response.read()
                declared = response.headers.get_content_charset()
                compression = response.headers.get("Content-Encoding", "").lower()
                final = response.url
        except urllib.error.HTTPError as exc:
            exc.close()
            raise
        finally:
            self.network_slots.release()
        self.check(cancelled)
        try:
            if "gzip" in compression or raw.startswith(b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            elif "deflate" in compression:
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except (OSError, zlib.error, EOFError) as exc:
            raise SourceError("书源返回的压缩页面不完整，无法解压。") from exc
        meta = re.search(br'<meta[^>]+charset\s*=\s*["\x27]?([\w-]+)', raw[:8192], re.I)
        candidates = [declared, meta[1].decode() if meta else None, encoding, "utf-8", "gb18030", "big5"]
        for charset in dict.fromkeys(item for item in candidates if item):
            try:
                return raw.decode(charset), final
            except (UnicodeError, LookupError):
                pass
        raise SourceError("无法识别书源页面编码。")

    def search_request(self, source, title, cancelled, opener, timeout=12):
        timeout = min(timeout, float(source.get("search_timeout", 5)))
        if source.get("kind") == "quanben":
            text, base = self.fetch(quanben_request(source["url"], title), cancelled, opener,
                                    referer=urllib.parse.urljoin(source["url"], "/search.html"), timeout=timeout)
            return quanben_result(text), base
        encoding = source.get("encoding", "utf-8")
        specification = source.get("search")
        if not specification:
            html, base = self.fetch(source["url"], cancelled, opener, timeout=timeout)
            document = Document(html).root
            for form in document.select("form"):
                inputs = form.select("input")
                field = next((node.attrs.get("name") for node in inputs
                              if node.attrs.get("name", "").lower() in {"q", "key", "keyword", "keywords", "searchkey", "keyboard", "searchword"}), None)
                if not field:
                    continue
                specification = {"url": urllib.parse.urljoin(base, form.attrs.get("action", "")),
                    "method": form.attrs.get("method", "GET"), "params": {node.attrs["name"]: node.attrs.get("value", "")
                    for node in inputs if node.attrs.get("name") and node.attrs.get("type", "").lower() == "hidden"}}
                specification["params"][field] = "{title}"
                break
            if not specification:
                raise SourceError("未找到可提交的书名搜索表单，需要更新此书源规则。")
        url = urllib.parse.urljoin(source["url"], specification.get("url", ""))
        url = url.replace("{title}", urllib.parse.quote(title, encoding=encoding))
        parameters = {key: str(value).replace("{title}", title) for key, value in specification.get("params", {}).items()}
        encoded = urllib.parse.urlencode(parameters, encoding=encoding)
        data = None
        if specification.get("method", "GET").upper() == "POST":
            data = encoded.encode("ascii")
        elif encoded:
            url += ("&" if "?" in url else "?") + encoded
        return self.fetch(url, cancelled, opener, data=data, encoding=encoding, referer=source["url"], timeout=timeout)

    def _fanqie_books(self, source, query, cancelled, timeout=12):
        url = urllib.parse.urljoin(source["url"], "/api/author/search/search_book/v1") + "?" + urllib.parse.urlencode(
            {"query_word": query, "page_count": 10, "page_index": 0, "query_type": 0, "filter": ""})
        text, _ = self.fetch(url, cancelled, referer=source["url"] + "search/" + urllib.parse.quote(query), timeout=timeout)
        try:
            result = json.loads(text)
        except ValueError as exc:
            raise SourceError("番茄接口未返回有效搜索数据。") from exc
        if result.get("code") != 0:
            raise SourceError(f"番茄搜索暂不可用：{result.get('message') or result.get('code')}")
        return result.get("data", {}).get("search_book_data_list", []) or []

    def _qidian_books(self, source, query, cancelled):
        url = urllib.parse.urljoin(source["url"], "/search") + "?" + urllib.parse.urlencode({"kw": query})
        html, _ = self.fetch(url, cancelled, timeout=4)
        match = re.search(r'<script[^>]*id=["\x27]vite-plugin-ssr_pageContext["\x27][^>]*>(.*?)</script>', html, re.S)
        if not match:
            raise SourceError("起点暂未返回作品目录数据。")
        data = json.loads(match[1])
        records = data.get("pageContext", {}).get("pageProps", {}).get("pageData", {}).get("bookInfo", {}).get("records", [])
        rows = []
        for book in records:
            if book.get("isPub") == 1:
                continue
            title = clean(Document(str(book.get("bName") or "")).root.text())
            author = clean(str(book.get("bAuth") or ""))
            book_id = str(book.get("bid") or "")
            if not title or not author or not book_id.isdigit():
                continue
            page = urllib.parse.urljoin(source["url"], "/book/" + book_id + "/")
            record = official_record(title, author, "起点", page, book.get("subCateName") or book.get("cat") or "")
            rows.append({"matched_title": title, "matched_author": author, "source": source["name"],
                "source_id": source["id"], "source_page": page, "url": page, "download_kind": "online_only",
                **({"platform_record": record} if record else {})})
        return rows

    def suggest(self, source, query, cancelled):
        """Read search-result titles only; never fetch book pages or chapters."""
        self.check(cancelled)
        if source.get("kind") == "lightnovel_catalog":
            return self.lightnovels.suggest(source, query, lambda: self.check(cancelled))
        if source.get("kind") == "qidian":
            rows = self._qidian_books(source, query, cancelled)
        elif source.get("kind") == "fanqie":
            rows = []
            for book in self._fanqie_books(source, query, cancelled, timeout=4):
                title, author = str(book.get("book_name") or ""), str(book.get("author") or "")
                page = urllib.parse.urljoin(source["url"], "/page/" + str(book.get("book_id") or ""))
                record = official_record(title, author, "番茄", page)
                rows.append({"matched_title": title, "matched_author": author, "source": source["name"],
                             **({"platform_record": record} if record else {})})
        else:
            html, base = self.search_request(source, query, cancelled, self.session(), timeout=4)
            document = Document(html).root
            rows = []
            for node in document.select(source.get("result_selector") or "a, h1"):
                self.check(cancelled)
                page = urllib.parse.urljoin(base, node.attrs.get("href", ""))
                if urllib.parse.urlsplit(page).scheme not in {"http", "https"}:
                    continue
                if node.tag == "a" and re.search(r"/(?:search|sort|category|rank|login|register)(?:[/.?]|$)", page, re.I):
                    continue
                name = re.sub(r"\s*(?:最新章节|全文阅读|全文閱讀|章节目录|TXT下载)\s*$", "", result_title(node, source, clean), flags=re.I)
                if name and len(name) <= 200 and not re.search(r"搜索结果|搜索結果|查询结果|第.{1,20}[章节回卷]", name):
                    rows.append({"matched_title": name, "matched_author": "", "source": source["name"]})
        return [row for row in rows if self.matcher.suggest(query, row["matched_title"])]

    def lookup(self, source, title, author, cancelled, suggestions=None, query=None):
        self.check(cancelled)
        suggestions = suggestions if suggestions is not None else []
        key = (json.dumps(source, ensure_ascii=False, sort_keys=True), self.normalize(title),
               self.author_key(author), self.normalize(query or title))
        with self.lock:
            cached = self.lookup_cache.get(key)
            if cached and time.monotonic() < cached[0]:
                found, rows = copy.deepcopy(cached[1:])
                suggestions.extend(rows)
                return found
        rows = []
        found = self._lookup(source, title, author, cancelled, rows, query)
        self.check(cancelled)
        with self.lock:
            if len(self.lookup_cache) >= 32:
                self.lookup_cache.pop(next(iter(self.lookup_cache)))
            self.lookup_cache[key] = (time.monotonic() + (120 if found else 30), copy.deepcopy(found), copy.deepcopy(rows))
        suggestions.extend(rows)
        return found

    def _lookup(self, source, title, author, cancelled, suggestions=None, query=None):
        suggestions = suggestions if suggestions is not None else []
        query = query or title
        if source.get("kind") == "lightnovel_catalog":
            return self.lightnovels.lookup(source, title, author, lambda: self.check(cancelled), suggestions)
        if source.get("kind") == "qidian":
            found = None
            for row in self._qidian_books(source, query, cancelled):
                match = self.matcher.candidate(title, row["matched_title"])
                if not match or author and self.author_key(author) != self.author_key(row["matched_author"]):
                    continue
                candidate = {**row, **match}
                suggestions.append(candidate)
                if match["match_kind"] == "exact":
                    found = candidate
            return found
        if source.get("kind") == "fanqie":
            for book in self._fanqie_books(source, query, cancelled):
                name, writer = str(book.get("book_name") or ""), str(book.get("author") or "")
                match = self.matcher.candidate(title, name)
                if not match or author and self.author_key(author) != self.author_key(writer):
                    continue
                page = urllib.parse.urljoin(source["url"], "/page/" + str(book["book_id"]))
                candidate = {"source": source["name"], "source_id": source["id"], "source_page": page, "url": page,
                             "matched_title": name, "matched_author": writer, "download_kind": "online_only", **match}
                record = official_record(name, writer, "番茄", page)
                if record:
                    candidate["platform_record"] = record
                suggestions.append(candidate)
                if match["match_kind"] == "exact":
                    return candidate
            return None
        opener = self.session()
        html, search_url = self.search_request(source, query, cancelled, opener)
        document = Document(html).root
        titles = document.select("title")
        if titles and re.search(r"just a moment|captcha|访问验证|人机验证|安全验证", titles[0].text(), re.I):
            raise SourceError("书源返回验证页面，没有搜索结果。")
        desired = self.normalize(title)
        result_nodes = document.select(source.get("result_selector") or "a")
        h1 = document.select("h1")
        if h1 and self.normalize(clean(h1[0].text())) == desired:
            candidates = [(search_url, document)]
        else:
            links = [node for node in result_nodes if node.attrs.get("href") and self.normalize(result_title(node, source, clean)) == desired]
            candidates = [(urllib.parse.urljoin(search_url, node.attrs["href"]), None) for node in links]
        similar = [(search_url, clean(h1[0].text()))] if h1 else []
        similar.extend((urllib.parse.urljoin(search_url, node.attrs["href"]), result_title(node, source, clean))
                       for node in result_nodes if node.attrs.get("href"))
        for page, name in similar:
            if len(name) > 200 or urllib.parse.urlsplit(page).scheme not in {"http", "https"}:
                continue
            if re.search(r"(?:搜索结果|搜索結果|查询结果|查詢結果)$", name):
                continue
            match = self.matcher.candidate(title, name)
            if not match or match["match_kind"] == "exact":
                continue
            suggestions.append({"source": source["name"], "source_id": source["id"], "source_page": page,
                "url": page, "matched_title": name, "matched_author": "", "local_path": "",
                "download_kind": "web_candidate", "web_source": source, **match})
        candidates = list({url: document for url, document in candidates}.items())
        for book_url, document in candidates:
            found = self.book(source, book_url, title, author, cancelled, opener, document, search_url)
            if found:
                return {**found, **self.matcher.match(title, found["matched_title"]), "match_confidence": "exact"}
        return None

    def book(self, source, book_url, title, author, cancelled, opener=None, document=None, referer=""):
        if source.get("kind") == "lightnovel_catalog":
            return self.lookup(source, title, author, cancelled)
        opener = opener or self.session()
        if document is None:
            html, book_url = self.fetch(book_url, cancelled, opener, encoding=source.get("encoding", ""), referer=referer,
                                        timeout=float(source.get("search_timeout", 5)))
            document = Document(html).root
        metadata = {node.attrs.get("property", node.attrs.get("name", "")): node.attrs.get("content", "") for node in document.select("meta")}
        headings = document.select(source.get("book_title_selector") or "h1")
        matched_title = metadata.get("og:novel:book_name") or (clean(headings[0].text()) if headings else "")
        if source.get("book_title_pattern"):
            match = re.search(source["book_title_pattern"], matched_title)
            matched_title = match[1].strip() if match else matched_title
        matched_author = metadata.get("og:novel:author", "")
        if not matched_author and source.get("author_selector"):
            authors = document.select(source["author_selector"])
            matched_author = clean(authors[0].text()) if authors else ""
            matched_author = re.sub(r"^作者[：:]\s*", "", matched_author)
        if not matched_author:
            match = re.search(r"作者[：:]\s*([^\n]{1,45})", document.text())
            matched_author = match[1].strip() if match else ""
            matched_author = re.split(r"日期[：:]|更新[：:]|最后更新", matched_author)[0].strip()
        if matched_author:
            matched_title = re.sub(r"\s*[（(]" + re.escape(matched_author) + r"[)）]\s*$", "", matched_title).strip()
        if self.normalize(matched_title) != self.normalize(title):
            return None
        if author and self.author_key(matched_author) != self.author_key(author):
            return None
        information = {"source": source["name"], "source_id": source["id"], "source_page": book_url,
            "matched_title": matched_title, "matched_author": matched_author, "local_path": "",
            "request_headers": {"Referer": book_url, "User-Agent": UA}}
        bulk_downloads, bulk_pages = self.bulk_links(source, document, book_url)
        recent = []
        if source.get("latest_selector"):
            recent = self.toc_chapters({**source, "chapter_selector": source["latest_selector"]}, document, book_url)
            if source.get("latest_exclude_selector"):
                excluded = {row["url"] for row in self.toc_chapters(
                    {**source, "chapter_selector": source["latest_exclude_selector"]}, document, book_url)}
                recent = [row for row in recent if row["url"] not in excluded]
            if source.get("latest_descending"):
                recent.reverse()
        for link in document.select("a"):
            target = urllib.parse.urljoin(book_url, link.attrs.get("href", ""))
            if not source.get("prefer_chapters") and urllib.parse.urlsplit(target).path.lower().endswith(".epub"):
                return {**information, "url": target}
        links = document.select("a")
        complete_toc = None
        if source.get("toc_link_selector"):
            choices = document.select(source["toc_link_selector"])
            complete_toc = choices[0] if choices else None
        if complete_toc is None and source.get("toc_link_pattern"):
            complete_toc = next((link for link in links if re.search(source["toc_link_pattern"],
                urllib.parse.urlsplit(urllib.parse.urljoin(book_url, link.attrs.get("href", ""))).path)), None)
        if complete_toc is None:
            complete_toc = next((link for link in links if clean(link.text()) in
                {"全部章节", "全部章節", "完整目录", "完整目錄", "全部目录", "查看全部章节", "查看完整目录", "章节列表",
                 "全部章节目录", "打开完整目录列表"}), None)
        toc_url = urllib.parse.urljoin(book_url, complete_toc.attrs.get("href", "")).split("#", 1)[0] if complete_toc else book_url
        if toc_url != book_url and urllib.parse.urlsplit(toc_url).netloc == urllib.parse.urlsplit(book_url).netloc:
            toc_html, toc_url = self.fetch(toc_url, cancelled, opener, encoding=source.get("encoding", ""), referer=book_url,
                                            timeout=float(source.get("search_timeout", 5)))
            document = Document(toc_html).root
        chapters = self.toc_chapters(source, document, toc_url)
        if not chapters:
            direct = next((url for url in bulk_downloads if urllib.parse.urlsplit(url).path.lower().endswith(".txt")), None)
            if direct:
                return {**information, "url": direct}
            raise SourceError("书名已匹配，但页面没有可获取的 TXT／EPUB 文件或章节目录。")
        return {**information, "url": book_url, "download_kind": "web_chapters", "web_source": source,
                "chapters": chapters, "expected_chapters": len(chapters), "toc_first_url": toc_url,
                "toc_pages": self.toc_page_links(source, document, toc_url),
                "recent_chapters": recent,
                "bulk_downloads": bulk_downloads, "bulk_pages": bulk_pages}

    @staticmethod
    def toc_chapters(source, document, base):
        chapter_nodes = document.select(source["chapter_selector"]) if source.get("chapter_selector") else document.select("a")
        chapters = {}
        for node in chapter_nodes:
            label = clean(node.text())
            href = node.attrs.get("href", "")
            all_toc_entries = source.get("include_all_toc_entries") and source.get("chapter_selector")
            if not href or not label or (not all_toc_entries and not re.search(r"第.{1,20}[章节回卷]|^序章|^楔子|^引子|^尾声|^后记|^自序|^前言|^序言|^引言|^序$", label)):
                continue
            if source.get("chapter_group_selector"):
                ancestor = node.parent
                while ancestor is not None:
                    if ancestor.matches(source["chapter_group_selector"]):
                        headings = ancestor.select(source["chapter_group_title_selector"])
                        if headings:
                            label = clean(headings[0].text()) + " · " + label
                            break
                    ancestor = ancestor.parent
            target = urllib.parse.urljoin(base, href).split("#", 1)[0]
            if (urllib.parse.urlsplit(target).scheme not in {"http", "https"}
                    or urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(base).netloc or target == base):
                continue
            # Keep the last occurrence so a latest-chapter shortcut cannot move it before chapter one.
            chapters.pop(target, None)
            chapters[target] = {"title": label, "url": target}
        return list(chapters.values())

    @staticmethod
    def toc_page_links(source, document, base):
        if not source.get("toc_page_selector") or not source.get("toc_page_pattern"):
            return []
        current = urllib.parse.urlsplit(base)
        pages = []
        for node in document.select(source["toc_page_selector"]):
            target = urllib.parse.urljoin(base, node.attrs.get("href", "")).split("#", 1)[0]
            parsed = urllib.parse.urlsplit(target)
            if (target != base and parsed.netloc == current.netloc and parsed.path.rsplit("/", 1)[0] == current.path.rsplit("/", 1)[0]
                    and re.search(source["toc_page_pattern"], parsed.path)):
                pages.append(target)
        return list(dict.fromkeys(pages))

    def complete_toc(self, task, cancelled):
        if not task.get("toc_pages"):
            if task.get("recent_chapters"):
                chapters = self.merge_recent(task["chapters"], task["recent_chapters"])
                task.update(chapters=chapters, expected_chapters=len(chapters))
            return
        source = task["web_source"]
        first = task["toc_first_url"]
        key = (json.dumps(source, ensure_ascii=False, sort_keys=True), first)
        with self.lock:
            cached = self.toc_cache.get(key)
            if cached and cached[0] > time.monotonic():
                chapters = self.merge_recent(copy.deepcopy(cached[1]), task.get("recent_chapters", []))
                task.update(chapters=chapters, expected_chapters=len(chapters), toc_pages=[])
                return
        stop = threading.Event()
        group = _StopGroup(cancelled, stop)
        documents, queued = {first: task["chapters"]}, {first}
        pending = {}
        pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="toc-page")

        def read(url):
            html, base = self.fetch(url, group, encoding=source.get("encoding", ""), referer=task["source_page"],
                                    timeout=float(source.get("search_timeout", 5)))
            document = Document(html).root
            chapters = self.toc_chapters(source, document, base)
            if not chapters:
                raise SourceContentError("目录分页没有章节，不能将前面的目录当作全书：" + url)
            return chapters, self.toc_page_links(source, document, base)

        def enqueue(urls):
            for url in urls:
                if url not in queued:
                    queued.add(url)
                    pending[pool.submit(read, url)] = url
        try:
            enqueue(task["toc_pages"])
            while pending:
                completed, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                self.check(cancelled)
                for future in completed:
                    url = pending.pop(future)
                    chapters, pages = future.result()
                    documents[url] = chapters
                    enqueue(pages)
            def order(url):
                match = re.search(source["toc_page_pattern"], urllib.parse.urlsplit(url).path)
                return int(match[1]) if match and match.lastindex else 0
            combined = {}
            for url in sorted(documents, key=order):
                for chapter in documents[url]:
                    combined.pop(chapter["url"], None)
                    combined[chapter["url"]] = chapter
            chapters = self.merge_recent(list(combined.values()), task.get("recent_chapters", []))
            task.update(chapters=chapters, expected_chapters=len(chapters), toc_pages=[])
            with self.lock:
                if len(self.toc_cache) >= 8:
                    self.toc_cache.pop(next(iter(self.toc_cache)))
                self.toc_cache[key] = (time.monotonic() + 120, copy.deepcopy(chapters))
        finally:
            stop.set()
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def merge_recent(chapters, recent):
        known = {row["url"] for row in chapters}
        if chapters and any(row["url"] not in known for row in recent):
            anchor = next((index for index, row in enumerate(recent) if row["url"] == chapters[-1]["url"]), None)
            if anchor is not None:
                chapters += [row for row in recent[anchor + 1:] if row["url"] not in known]
        return chapters

    @staticmethod
    def bulk_links(source, document, base, landing=False):
        if source.get("prefer_chapters"):
            return [], []
        direct, pages = [], []
        for node in document.select("a" if landing else source.get("bulk_selector", "a")):
            href, label = node.attrs.get("href", ""), clean(node.text())
            if not href:
                continue
            url = urllib.parse.urljoin(base, href).split("#", 1)[0]
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            query = dict(urllib.parse.parse_qsl(parsed.query))
            download_label = re.search(r"(?:txt|全文|全集|整本).*(?:下载|下載)|(?:下载|下載).*(?:txt|全文|全集|整本)", label, re.I)
            is_text = parsed.path.lower().endswith(".txt") or query.get("type", query.get("format", "")).lower() == "txt"
            is_archive = parsed.path.lower().endswith(".zip") and download_label
            encoded = urllib.parse.quote(url, safe=":/?=&%+@;,$!~*'()[]")
            if is_text or is_archive:
                direct.append(encoded)
            elif not landing and download_label and parsed.netloc == urllib.parse.urlsplit(base).netloc and url != base:
                pages.append(encoded)
        return list(dict.fromkeys(direct)), list(dict.fromkeys(pages))

    def fetch_bulk(self, url, task, cancelled, progress):
        source = task["web_source"]
        timeout = float(source.get("bulk_timeout", 8))
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": task["source_page"],
            "Accept": "text/plain,application/zip,application/octet-stream;q=0.9,*/*;q=0.5", "Accept-Encoding": "gzip, deflate"})
        while not self.network_slots.acquire(timeout=.1):
            self.check(cancelled)
        try:
            self.check(cancelled)
            start = time.monotonic()
            chunks, received = [], 0
            with self.session().open(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length", "")
                compression = response.headers.get("Content-Encoding", "").lower()
                declared = response.headers.get_content_charset() or source.get("encoding", "")
                final = response.url
                while True:
                    self.check(cancelled)
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    progress(received, 0, 1)
                    if time.monotonic() - start > float(source.get("bulk_deadline", 30)):
                        raise SourceError("整本下载等待过长，改为获取章节。")
                if length.isdigit() and received != int(length):
                    raise SourceError("整本文件传输未完成，改为获取章节。")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise
        finally:
            self.network_slots.release()
        raw = b"".join(chunks)
        self.check(cancelled)
        if "gzip" in compression or raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        elif "deflate" in compression:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return text_files(raw, declared), final

    def prefetch_bulk(self, task, cancelled, progress, cache_dir, reuse_dirs):
        attempts = task.setdefault("bulk_attempts", [])
        source = task["web_source"]
        cached_added = 0
        if source.get("bulk_numbered_toc"):
            extras = [row for directory in (cache_dir, *reuse_dirs) for row in cached_bulk_extras(directory)]
            expanded = merge_cached_extras(task["chapters"], extras, self.normalize)
            cached_added = len(expanded) - len(task["chapters"])
            task["chapters"], task["expected_chapters"] = expanded, len(expanded)
        known = set()
        copied = []
        for chapter in task["chapters"]:
            self.check(cancelled)
            for directory in (cache_dir, *reuse_dirs):
                row = read_checkpoint(directory, chapter)
                if row:
                    known.add(chapter["url"])
                    if directory != cache_dir:
                        copied.append(row)
                    break
        if copied:
            write_bulk_checkpoints(cache_dir, copied)
        if len(known) == len(task["chapters"]):
            return {"bulk_chapters": 0, "bulk_added_chapters": cached_added, "bulk_attempts": attempts,
                    "acquisition_method": "chapter_cache"}
        urls = list(task.get("bulk_downloads", []))
        attempted = {row["url"] for row in attempts}
        for page in task.get("bulk_pages", []):
            if page in attempted:
                continue
            self.check(cancelled)
            try:
                html, final = self.fetch(page, cancelled, encoding=source.get("encoding", ""),
                                         referer=task["source_page"], timeout=float(source.get("bulk_timeout", 8)))
                links, _ = self.bulk_links(source, Document(html).root, final, landing=True)
                urls.extend(links)
                attempts.append({"url": page, "state": "resolved", "files": links})
            except SourceCancelled:
                raise
            except Exception as exc:
                attempts.append({"url": page, "state": "failed", "error": str(exc)})
        imported, added = 0, cached_added
        method = ""
        for url in dict.fromkeys(urls):
            if url in attempted:
                continue
            self.check(cancelled)
            try:
                files, final = self.fetch_bulk(url, task, cancelled, progress)
                choices = []
                for name, text in files:
                    self.check(cancelled)
                    sections = parse_sections(text, task["chapters"], self.normalize, source.get("bulk_numbered_toc", False))
                    matched, chapters, extra = match_sections(sections, task["chapters"], self.normalize, final,
                                                              source.get("bulk_numbered_toc", False))
                    choices.append((len(matched), name, matched, chapters, extra))
                count, name, matched, chapters, extra = max(choices, key=lambda row: row[0])
                if count < max(1, len(task["chapters"]) / 2):
                    raise SourceContentError("整本文件无法按当前目录定位多数章节，改为逐章获取。")
                task["chapters"], task["expected_chapters"] = chapters, len(chapters)
                def rows():
                    for chapter in chapters:
                        self.check(cancelled)
                        row = matched.get(chapter["url"])
                        if row and chapter["url"] not in known:
                            yield write_checkpoint(None, chapter, row["text"], [final])
                imported += write_bulk_checkpoints(cache_dir, rows())
                known.update(matched)
                added += extra
                method = "source_text_archive" if name else "source_text_file"
                attempts.append({"url": url, "final_url": final, "state": "imported", "matched_chapters": count,
                                 "added_chapters": extra, "member": name})
                # One good file supplies the book; only the missing chapters need HTTP fallback.
                break
            except SourceCancelled:
                raise
            except Exception as exc:
                attempts.append({"url": url, "state": "failed", "error": str(exc)})
        return {"bulk_chapters": imported, "bulk_added_chapters": added, "bulk_attempts": attempts,
                "acquisition_method": method or "source_chapters"}

    def source_ready(self, source):
        with self.lock:
            return float(self.status.get(source["id"], {}).get("retry_after", 0)) <= time.time()

    @staticmethod
    def source_candidate(found):
        row = {key: value for key, value in found.items() if key not in
               {"chapters", "source_pages", "toc_pages", "toc_first_url", "recent_chapters", "bulk_downloads", "bulk_pages", "bulk_attempts"}}
        if row.get("download_kind") == "web_chapters":
            row.update(download_kind="web_candidate", url=row["source_page"])
        return row

    def find(self, sources, title, author, cancelled, attempts, suggestions=None, preferred_author=""):
        suggestions = suggestions if suggestions is not None else []
        preferred_author = preferred_author or author
        deadline = time.monotonic() + self.search_budget
        stopped = threading.Event()
        group = _StopGroup(cancelled, stopped)
        available, responsive, matches = [], [], []
        first_match = None

        def worker(source, query):
            errors = []
            for base in [source["url"], *source.get("mirrors", [])]:
                self.check(group)
                actual, rows = {**source, "url": base}, []
                try:
                    found = self.lookup(actual, title, author, group, rows, query=query)
                    return actual, found, rows, "", errors
                except SourceCancelled:
                    raise
                except Exception as exc:
                    errors.append({"url": base, "error": str(exc)})
            return source, None, [], errors[-1]["error"], errors

        def receive(index, source, query, result):
            nonlocal first_match
            actual, found, rows, error, mirror_errors = result
            suggestions.extend(rows)
            extra = {"query": query} if query and query != title else {}
            if mirror_errors:
                extra["mirror_errors"] = mirror_errors
            if error:
                self.record(source, "unavailable", error, query=title, retry_after=time.time() + 60)
                attempts.append({"source": source["name"], "status": "unavailable", "error": error, **extra})
                return
            self.record(source, "available", "已完成书名检索", query=title, matched=bool(found), retry_after=0)
            responsive.append((index, source))
            if not found:
                attempts.append({"source": source["name"], "status": "candidates" if rows else "not_found", "error": "", **extra})
                return
            candidate = self.source_candidate(found)
            if found.get("download_kind") == "online_only":
                attempts.append({"source": source["name"], "status": "online_only", "error": "已找到作品，来源未提供全文文件。", "match": candidate, **extra})
                if candidate not in suggestions:
                    suggestions.append(candidate)
                return
            if preferred_author and self.author_key(found.get("matched_author", "")) != self.author_key(preferred_author):
                candidate.update(match_kind="same_title", match_confidence="needs_choice")
                suggestions[:] = [row for row in suggestions if (row.get("source_id"), row.get("matched_title"), row.get("matched_author")) !=
                                  (candidate.get("source_id"), candidate.get("matched_title"), candidate.get("matched_author"))]
                suggestions.append(candidate)
                attempts.append({"source": source["name"], "status": "other_author", "error": "同名作品作者不同，保留候选。", "match": candidate, **extra})
                return
            suggestions.append(candidate)
            matches.append((index, found))
            first_match = first_match or time.monotonic()
            attempts.append({"source": source["name"], "status": "matched", "error": "", **extra})

        for index, source in enumerate(sources):
            if not source.get("enabled"):
                continue
            self.check(cancelled)
            if not self.source_ready(source):
                attempts.append({"source": source["name"], "status": "cooldown", "error": "暂时跳过刚失败的来源，稍后自动重试。"})
                continue
            if source.get("kind") == "lightnovel_catalog":
                receive(index, source, title, worker(source, title))
                if matches:
                    return matches[0][1]
            else:
                available.append((index, source))
        pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="source-search")

        def run(jobs):
            pending = {pool.submit(worker, source, query): (index, source, query) for index, source, query in jobs}
            try:
                while pending and time.monotonic() < deadline:
                    self.check(cancelled)
                    completed, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                    for future in completed:
                        index, source, query = pending.pop(future)
                        receive(index, source, query, future.result())
                    if matches:
                        best = min(index for index, _ in matches)
                        age = time.monotonic() - first_match
                        if age >= .25 and (not any(index < best for index, _, _ in pending.values()) or age >= 1.2):
                            break
                if pending and not matches and time.monotonic() >= deadline:
                    for _, source, _ in pending.values():
                        attempts.append({"source": source["name"], "status": "timeout", "error": "本次检索等待超时。"})
                        self.record(source, "unavailable", "本次检索等待超时。", retry_after=time.time() + 60)
            finally:
                for future in pending:
                    future.cancel()
        try:
            run([(index, source, title) for index, source in available])
            if not matches and not suggestions and time.monotonic() < deadline:
                retries = []
                for index, source in sorted(dict((source["id"], (index, source)) for index, source in responsive).values()):
                    if source.get("kind") not in {"lightnovel_catalog", "fanqie", "qidian"} and source.get("retry_keywords", True):
                        retries.extend((index, source, query) for query in self.matcher.queries(title))
                        if len(retries) >= 8:
                            break
                run(retries)
            if not matches:
                return None
            identities = {(self.normalize(row["matched_title"]), self.author_key(row.get("matched_author", ""))) for _, row in matches}
            if not author and not preferred_author and len(identities) > 1:
                for candidate in suggestions:
                    if candidate.get("match_kind") in {"exact", "alias"}:
                        candidate.update(match_kind="same_title", match_confidence="needs_choice")
                return None
            return min(matches, key=lambda row: row[0])[1]
        finally:
            stopped.set()
            pool.shutdown(wait=False, cancel_futures=True)

    def next_page(self, document, html, final, source):
        links = document.select("a")
        if source.get("next_page_selector"):
            following = document.select(source["next_page_selector"])
            if following:
                return urllib.parse.urljoin(final, following[0].attrs.get("href", ""))
        explicit = next((link for link in links if clean(link.text()) in {"下一页", "下页", "下一頁", "下頁"}), None)
        if explicit:
            return urllib.parse.urljoin(final, explicit.attrs.get("href", ""))
        if source.get("next_page_pattern"):
            match = re.search(source["next_page_pattern"], html)
            if match:
                return urllib.parse.urljoin(final, match[1])
        current = urllib.parse.urlsplit(final)
        part = re.fullmatch(r"(.*?)(?:_(\d+))?(\.html?)", current.path)
        if part:
            number = int(part[2] or 1)
            for link in links:
                target = urllib.parse.urljoin(final, link.attrs.get("href", ""))
                parsed = urllib.parse.urlsplit(target)
                following = re.fullmatch(re.escape(part[1]) + r"_(\d+)" + re.escape(part[3]), parsed.path)
                if parsed.netloc == current.netloc and following and int(following[1]) == number + 1:
                    return target
        return ""

    def chapter(self, task, chapter, cancelled, opener=None, chapter_urls=None):
        if chapter.get("bulk_only"):
            raise SourceContentError("此章节由整本文件补入，当前没有单章页面。")
        source, opener = task["web_source"], opener or self.session()
        url, parts, pages, visited = chapter["url"], [], [], set()
        if chapter_urls is None:
            chapter_urls = {row["url"] for row in task["chapters"]}
        while url:
            self.check(cancelled)
            if url in visited:
                raise SourceContentError(f"章节分页出现循环：{chapter['title']}")
            visited.add(url)
            for attempt in range(3):
                try:
                    html, final = self.fetch(url, cancelled, opener, encoding=source.get("encoding", ""), referer=task["source_page"])
                    break
                except OSError as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 429, 500, 502, 503, 504}:
                        raise
                    if attempt == 2:
                        raise
                    if cancelled.wait(.4 * (attempt + 1)):
                        raise SourceCancelled("任务已停止。")
            document = Document(html).root
            contents = document.select(source.get("content_selector") or "#content, #chaptercontent, .read_chapterDetail, .read-content")
            text = clean("\n".join(node.text() for node in contents))
            problem = chapter_problem(text)
            if problem:
                raise SourceContentError(f"{problem}：{chapter['title']}")
            for pattern in source.get("content_remove_patterns", []):
                text = re.sub(pattern, "", text)
            text = clean(text)
            if chapter_problem(text):
                raise SourceContentError(f"章节正文为空：{chapter['title']}")
            parts.append(text)
            pages.append(final)
            target = self.next_page(document, html, final, source)
            if target in chapter_urls or urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(final).netloc:
                target = ""
            url = target
        return "\n\n".join(parts), pages

    def prefetch(self, task, cancelled, progress, cache_dir, repair=None, workers=4, reuse_dirs=()):
        self.complete_toc(task, cancelled)
        if cache_dir is not None:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
        if "bulk_downloads" not in task and task.get("web_source"):
            # Older saved tasks have a TOC but no whole-book discovery metadata.
            previous = task["web_source"]
            defaults = next((row for row in DEFAULT_SOURCES if row["id"] == previous.get("id")), {})
            task["web_source"] = {**defaults, **previous}
            task.update(bulk_downloads=[], bulk_pages=[])
            try:
                fresh = self.book(task["web_source"], task["source_page"], task["matched_title"],
                                  task.get("matched_author", ""), cancelled)
                if fresh:
                    task.update(bulk_downloads=fresh.get("bulk_downloads", []), bulk_pages=fresh.get("bulk_pages", []))
            except SourceCancelled:
                raise
            except (SourceError, OSError):
                pass
        bulk = None
        if cache_dir is not None and (task.get("bulk_downloads") or task.get("bulk_pages")):
            bulk = self.prefetch_bulk(task, cancelled, progress, cache_dir, reuse_dirs)
        stopped = threading.Event()
        pending_stop = _StopGroup(cancelled, stopped)
        sessions = threading.local()
        chapter_urls = {row["url"] for row in task["chapters"]}

        def load(chapter):
            self.check(pending_stop)
            row = read_checkpoint(cache_dir, chapter)
            needs_write = not bool(row)
            if not row:
                for directory in reuse_dirs:
                    row = read_checkpoint(directory, chapter)
                    if row:
                        break
            if row:
                return row, True, needs_write
            provenance = None
            try:
                if not hasattr(sessions, "opener"):
                    sessions.opener = self.session()
                text, pages = self.chapter(task, chapter, pending_stop, sessions.opener, chapter_urls)
            except SourceCancelled:
                raise
            except (SourceError, OSError) as exc:
                if repair is None:
                    raise
                text, pages, provenance = repair(chapter, exc)
            self.check(pending_stop)
            return write_checkpoint(None, chapter, text, pages, provenance), False, True

        count = received = reused = 0
        seen_pages = set()
        workers = max(1, min(int(workers), int(task.get("web_source", {}).get("chapter_workers", 6)), 6))
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chapter-fetch")
        pending, chapters = set(), iter(task["chapters"])

        def fill():
            while len(pending) < workers * 2:
                chapter = next(chapters, None)
                if chapter is None:
                    break
                pending.add(pool.submit(load, chapter))

        def persist(row):
            write_checkpoint(cache_dir, row, row["text"], row["pages"], row.get("repair"), create_directory=False)

        try:
            fill()
            while pending:
                completed, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                self.check(cancelled)
                for future in completed:
                    row, cached, needs_write = future.result()
                    if needs_write:
                        persist(row)
                    pending.remove(future)
                    count += 1
                    reused += cached
                    received += len(row["text"].encode("utf-8"))
                    seen_pages.update(row["pages"])
                    progress(received, count, len(seen_pages))
                    fill()
        finally:
            # On a chapter error, finish already-running chapters so their valid
            # text can be cached; queued work is cancelled by shutdown below.
            # An explicit user stop still interrupts running work immediately.
            if cancelled.is_set():
                stopped.set()
            pool.shutdown(wait=True, cancel_futures=True)
            stopped.set()
            for future in pending:
                if future.cancelled():
                    continue
                try:
                    row, _, needs_write = future.result()
                except Exception:
                    continue
                if needs_write:
                    persist(row)
        statistics = {"resumed_chapters": reused, "network_chapters": count - reused}
        if bulk is not None:
            statistics.update(bulk)
            statistics["resumed_chapters"] -= bulk["bulk_chapters"]
            if bulk["bulk_chapters"] and statistics["network_chapters"]:
                statistics["acquisition_method"] = "source_bulk_and_chapters"
            statistics["expected_chapters"] = len(task["chapters"])
        return statistics

    def export(self, task, destination, cancelled, progress, cache_dir=None, repair=None):
        opener = self.session()
        chapter_urls = {row["url"] for row in task["chapters"]}
        completed, pages, repairs, resumed = [], [], [], 0
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            header = task["matched_title"] + "\n作者：" + task.get("matched_author", "") + "\n来源：" + task["source_page"] + "\n"
            handle.write(header)
            received = len(header.encode("utf-8"))
            for chapter in task["chapters"]:
                self.check(cancelled)
                cached = read_checkpoint(cache_dir, chapter)
                if cached:
                    resumed += 1
                else:
                    replacement = None
                    try:
                        text, chapter_pages = self.chapter(task, chapter, cancelled, opener, chapter_urls)
                    except SourceCancelled:
                        raise
                    except (SourceError, OSError) as exc:
                        if repair is None:
                            raise
                        text, chapter_pages, replacement = repair(chapter, exc)
                    cached = write_checkpoint(cache_dir, chapter, text, chapter_pages, replacement)
                if cached.get("repair"):
                    repairs.append(cached["repair"])
                pages.extend(cached["pages"])
                section = "\n\n" + chapter["title"] + "\n\n" + clean(cached["text"]) + "\n"
                handle.write(section)
                received += len(section.encode("utf-8"))
                completed.append(chapter["title"])
                progress(received, len(completed), len(pages))
        pages = list(dict.fromkeys(pages))
        return {"source_pages": pages, "page_count": len(pages), "chapter_count": len(completed),
                "expected_chapters": len(task["chapters"]), "resumed_chapters": resumed,
                "chapter_repairs": repairs, "content_issues": [], "content_export_version": 2}
