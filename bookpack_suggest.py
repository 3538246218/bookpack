"""Read-only, cached title completion for the search input. Standard library only."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed


class SuggestionService:
    def __init__(self, search):
        self.search = search
        self.normalize = search.matcher.normalize
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="title-suggest")
        self.remote_cache = {}
        self.local_cache = None
        self.clients = {}

    def close(self):
        with self.lock:
            for _, event, _ in self.clients.values():
                event.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _local(self, directories):
        key = tuple(directories)
        with self.lock:
            if self.local_cache and self.local_cache[0] == key and time.monotonic() - self.local_cache[1] < 10:
                return self.local_cache[2]
        rows, _ = self.search.local_entries(directories, threading.Event())
        with self.lock:
            self.local_cache = (key, time.monotonic(), rows)
        return rows

    def _rank(self, query, author, rows):
        self.search.platforms.observe(rows)
        priority = {"exact": 0, "prefix": 1, "contains": 2, "fuzzy": 3}
        candidates = []
        for row in rows:
            title = row.get("matched_title") or row.get("title", "")
            writer = row.get("matched_author") or row.get("author", "")
            if author and writer and self.normalize(author) != self.normalize(writer):
                continue
            match = self.search.matcher.suggest(query, title, row.get("aliases", []))
            if match:
                candidates.append(self.search.platforms.annotate({"title": title, "author": writer,
                    "source": row.get("source", "已检索书籍"), **match}))
        def order(row):
            if row["suggestion_kind"] == "prefix":
                return (priority["prefix"], not row["platform_verified"],
                        -len(self.normalize(row["matched_on"])), -row["suggestion_score"])
            return (priority[row["suggestion_kind"]], -row["suggestion_score"], not row["platform_verified"])
        candidates.sort(key=order)
        seen, result = set(), []
        for candidate in candidates:
            identity = (self.normalize(candidate["title"]), self.normalize(candidate["author"]))
            if identity in seen:
                continue
            seen.add(identity)
            result.append(candidate)
        return result[:8]

    def _client_event(self, client, generation):
        cancelled = threading.Event()
        if not client:
            return cancelled
        with self.lock:
            previous = self.clients.get(client)
            if previous and generation < previous[0]:
                cancelled.set()
                return cancelled
            if previous:
                previous[1].set()
            self.clients[client] = (generation, cancelled, time.monotonic())
            if len(self.clients) > 32:
                oldest = min(self.clients, key=lambda key: self.clients[key][2])
                self.clients.pop(oldest)[1].set()
        return cancelled

    def _remote(self, source, query, cancelled):
        if source.get("kind") == "wikisource_suggest":
            self.search._check(cancelled)
            result = self.search._json({"action": "query", "format": "json", "list": "search",
                "srsearch": query, "srnamespace": 0, "srlimit": 8}, timeout=4)
            self.search._check(cancelled)
            return [{"matched_title": page["title"], "source": "维基文库"}
                    for page in result.get("query", {}).get("search", []) if "/" not in page.get("title", "")]
        rows = self.search.sources.suggest(source, query, cancelled)
        if not rows and source.get("retry_keywords", True):
            for variant in self.search.matcher.queries(query):
                self.search._check(cancelled)
                rows = [row for row in self.search.sources.suggest(source, variant, cancelled)
                        if self.search.matcher.suggest(query, row["matched_title"])]
                if rows:
                    break
        return rows

    def lookup(self, value, recent=(), online=False, client_id="", generation=0):
        value = str(value or "").strip()[:200]
        parts = re.split(r"[|\t]", value, maxsplit=1)
        query, author = parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
        if len(self.normalize(query)) < 2:
            return {"query": value, "suggestions": [], "online_pending": False}
        config = self.search.settings()
        local_novels = []
        for source in config["web_sources"]:
            if source.get("enabled") and source.get("kind") == "lightnovel_catalog":
                try:
                    local_novels.extend(self.search.sources.suggest(source, query, threading.Event()))
                except (OSError, ValueError, KeyError):
                    pass
        sources = [source for source in config["web_sources"] if source.get("enabled") and self.search.sources.source_ready(source)
                   and source.get("kind") != "lightnovel_catalog"]
        sources.sort(key=lambda source: source.get("kind") not in {"qidian", "fanqie"})
        if config["wikisource"]:
            sources.append({"id": "wikisource-suggest", "kind": "wikisource_suggest", "url": self.search.wiki_api})
        key = (self.normalize(query), json.dumps(sources, ensure_ascii=False, sort_keys=True))
        rows = self.search.platforms.suggestions(query) + config["books"] + self._local(config["local_dirs"]) + list(recent) + local_novels
        with self.lock:
            cached = self.remote_cache.get(key)
            if cached and time.monotonic() - cached[0] >= 120:
                cached = None
        if cached:
            rows += cached[1]
        if not online or not sources or cached:
            return {"query": value, "suggestions": self._rank(query, author, rows),
                    "online_pending": bool(sources) and not online and not cached}

        cancelled = self._client_event(str(client_id), int(generation))
        if cancelled.is_set():
            return {"query": value, "suggestions": [], "online_pending": False, "cancelled": True}
        futures = [self.pool.submit(self._remote, source, query, cancelled) for source in sources]
        remote, successful, incomplete = [], 0, False
        try:
            for future in as_completed(futures, timeout=6):
                if cancelled.is_set():
                    break
                try:
                    remote.extend(future.result())
                    successful += 1
                except Exception:
                    incomplete = True
        except TimeoutError:
            incomplete = True
        finally:
            stale = cancelled.is_set()
            cancelled.set()
            for future in futures:
                future.cancel()
        if stale:
            return {"query": value, "suggestions": [], "online_pending": False, "cancelled": True}
        # A failed request must not suppress a subsequent attempt for two minutes.
        if successful:
            with self.lock:
                self.remote_cache[key] = (time.monotonic(), remote)
                if len(self.remote_cache) > 128:
                    oldest = min(self.remote_cache, key=lambda item: self.remote_cache[item][0])
                    del self.remote_cache[oldest]
        return {"query": value, "suggestions": self._rank(query, author, rows + remote),
                "online_pending": False, "partial": incomplete, "online_unavailable": successful == 0}
