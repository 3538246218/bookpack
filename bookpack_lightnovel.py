"""Local title index for the public Wenku8 ebook catalog; no body crawling for suggestions."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from difflib import get_close_matches
from pathlib import Path

CATALOG_URL = "https://wenku.mojimoon.top/"
CATALOG_FILE = Path(__file__).resolve().parent / "data" / "lightnovel_catalog.json"


def catalog_entries(data):
    entries = []
    for item in data.get("items", []):
        title, book_id = str(item.get("t", "")).strip(), str(item.get("n", ""))
        if not title or not book_id.isdigit():
            continue
        aliases = [name.strip() for name in re.split(r"[／/、|;；]", str(item.get("a", ""))) if name.strip()]
        raw_url = str(item.get("x", "")).strip()
        download = (raw_url if raw_url.startswith(("https://", "http://")) else
                    "https://raw.githubusercontent.com/" + raw_url) if raw_url else ""
        entries.append({"id": book_id, "title": title, "author": str(item.get("au", "")).strip(),
                        "aliases": aliases, "url": urllib.parse.quote(download, safe=":/?=&%"),
                        "source_page": "https://www.wenku8.net/book/" + book_id + ".htm",
                        "file_updated_at": str(item.get("xu", "")), "work_updated_at": str(item.get("u", "")),
                        "catalog_volume_label": str(item.get("v", ""))})
    return entries


class LightNovelCatalog:
    def __init__(self, matcher):
        self.matcher, self.normalize = matcher, matcher.normalize
        self.lock = threading.RLock()
        self.cache = {}

    def load(self, source):
        path = Path(source.get("catalog_file") or CATALOG_FILE)
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            data = json.loads(path.read_text(encoding="utf-8"))
            index, by_id = {}, {}
            for item in data["books"]:
                by_id[item["id"]] = item
                for name in (item["title"], *item.get("aliases", [])):
                    normalized = self.normalize(name)
                    if normalized:
                        index.setdefault(normalized, []).append(item)
            self.cache = {key: (data, index, by_id)}
            return self.cache[key]

    def possible(self, source, query, check):
        check()
        _, index, _ = self.load(source)
        wanted = self.normalize(query)
        names = [name for name in index if name == wanted or name.startswith(wanted)
                 or len(wanted) >= 3 and wanted in name]
        names.sort(key=lambda name: (name != wanted, not name.startswith(wanted),
                                     -len(name) if name.startswith(wanted) else len(name)))
        names = names[:64]
        if len(wanted) >= 3:
            names += get_close_matches(wanted, index, n=16, cutoff=.55)
        result, seen = [], set()
        for name in dict.fromkeys(names):
            check()
            for item in index[name]:
                if item["id"] not in seen:
                    result.append(item)
                    seen.add(item["id"])
        return result

    @staticmethod
    def record(source, item):
        file_date = item.get("file_updated_at", "")
        work_date = item.get("work_updated_at", "")
        note = "整理文件日期：" + (file_date or "未注明")
        if work_date:
            note += "；作品目录更新：" + work_date
        note += "；卷数以实际文件内容为准，可能未含后续更新或插图。"
        return {"source": source["name"], "source_id": source["id"], "source_page": item["source_page"],
                "matched_title": item["title"], "matched_author": item.get("author", ""),
                "aliases": item.get("aliases", []), "url": item.get("url") or item["source_page"], "local_path": "",
                "download_kind": "source_file" if item.get("url") else "online_only", "lightnovel_id": item["id"],
                "lightnovel_aliases": item.get("aliases", []),
                "source_updated_at": file_date, "work_updated_at": work_date, "edition_note": note,
                "request_headers": {"Referer": source["url"]}}

    def suggest(self, source, query, check):
        return [self.record(source, item) for item in self.possible(source, query, check)
                if self.matcher.suggest(query, item["title"], item.get("aliases", []))]

    def lookup(self, source, title, author, check, suggestions):
        found = []
        for item in self.possible(source, title, check):
            if author and self.normalize(author) != self.normalize(item.get("author", "")):
                continue
            match = self.matcher.candidate(title, item["title"], item.get("aliases", []))
            if not match:
                continue
            row = {**self.record(source, item), **match}
            suggestions.append(row)
            if match["match_kind"] in {"exact", "alias"}:
                found.append(row)
        if len(found) == 1:
            return found[0]
        return None


def verify_epub_title(path, task, normalize):
    """File identity comes from the EPUB title; this catalog's creator fields are not reliable."""
    expected = {normalize(name) for name in (task["matched_title"], *task.get("lightnovel_aliases", []))}
    with zipfile.ZipFile(path) as archive:
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
        except (ET.ParseError, KeyError) as exc:
            raise ValueError("轻小说 EPUB 的目录信息无法读取。") from exc
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is None:
            raise ValueError("轻小说 EPUB 未提供内容目录文件。")
        try:
            metadata = ET.fromstring(archive.read(rootfile.attrib["full-path"]))
        except (ET.ParseError, KeyError) as exc:
            raise ValueError("轻小说 EPUB 的作品信息无法读取。") from exc
        titles = [normalize(node.text or "") for node in metadata.iter("{http://purl.org/dc/elements/1.1/}title")]
        if not any(title in expected for title in titles):
            raise ValueError("下载 EPUB 的实际书名与所选轻小说不符。")
