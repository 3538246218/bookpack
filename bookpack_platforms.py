"""Work-platform metadata, independent of download sources. Standard library only."""
from __future__ import annotations

import json
import threading
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import urlsplit

CATALOG_FILE = Path(__file__).resolve().parent / "data" / "platform_catalog.json"
OFFICIAL_HOSTS = {
    "番茄": ("fanqienovel.com",), "起点": ("qidian.com",),
    "纵横": ("zongheng.com",), "晋江": ("jjwxc.net",),
    "七猫": ("qimao.com",), "刺猬猫": ("ciweimao.com",),
    "17K": ("17k.com",), "QQ阅读": ("book.qq.com",),
}
PLATFORM_KEYS = ("platform_label", "platform_verified", "platform_records", "platform_category")


def official_record(title, author, platform, url, category="", aliases=(), evidence="official_search"):
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower()
    roots = OFFICIAL_HOSTS.get(platform, ())
    if (parsed.scheme not in {"http", "https"} or not title or not author
            or not any(host == root or host.endswith("." + root) for root in roots)):
        return None
    return {"title": str(title), "author": str(author), "platform": platform, "official_url": str(url),
            "category": str(category), "aliases": list(aliases), "evidence": str(evidence)}


class WorkPlatforms:
    def __init__(self, home, normalize, catalog_file=CATALOG_FILE):
        self.normalize = normalize
        self.lock = threading.RLock()
        self.file = Path(home) / "平台索引.json"
        self.records = {}
        self.by_name = {}
        for path in (Path(catalog_file), self.file):
            try:
                for row in json.loads(path.read_text(encoding="utf-8-sig")).get("books", []):
                    self._put(row)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        self._index()

    def _put(self, value):
        record = official_record(value.get("title"), value.get("author"), value.get("platform"),
            value.get("official_url", ""), value.get("category", ""), value.get("aliases", []), value.get("evidence", "official_catalog"))
        if not record:
            return False
        key = (self.normalize(record["title"]), self.normalize(record["author"]), record["platform"])
        previous = self.records.get(key)
        if previous:
            record["aliases"] = list(dict.fromkeys(previous.get("aliases", []) + record["aliases"]))
            record["category"] = record["category"] or previous.get("category", "")
        if previous == record:
            return False
        self.records[key] = record
        return True

    def _index(self):
        self.by_name = {}
        for row in self.records.values():
            for title in (row["title"], *row.get("aliases", [])):
                self.by_name.setdefault(self.normalize(title), []).append(row)

    def observe(self, rows):
        with self.lock:
            changed = False
            for row in rows:
                record = row.get("platform_record")
                if not record:
                    continue
                title = row.get("matched_title") or row.get("title", "")
                author = row.get("matched_author") or row.get("author", "")
                if (self.normalize(title) == self.normalize(record.get("title", ""))
                        and self.normalize(author) == self.normalize(record.get("author", ""))):
                    changed = self._put(record) or changed
            if changed:
                self._index()
                temporary = self.file.with_suffix(".tmp")
                temporary.write_text(json.dumps({"books": list(self.records.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.file)

    def annotate(self, row):
        title = row.get("matched_title") or row.get("title", "")
        author = row.get("matched_author") or row.get("author", "")
        with self.lock:
            records = [record for record in self.by_name.get(self.normalize(title), [])
                       if author and self.normalize(record["author"]) == self.normalize(author)]
        platforms = list(dict.fromkeys(record["platform"] for record in records))
        categories = list(dict.fromkeys(record["category"] for record in records if record.get("category")))
        return {**row, "platform_label": " / ".join(platforms) if platforms else "平台未核实",
                "platform_verified": bool(platforms), "platform_records": records,
                "platform_category": " / ".join(categories[:2])}

    def suggestions(self, query):
        wanted = self.normalize(query)
        if len(wanted) < 2:
            return []
        with self.lock:
            names = [name for name in self.by_name if name.startswith(wanted) or len(wanted) >= 3 and wanted in name]
            names.sort(key=lambda name: (name != wanted, not name.startswith(wanted),
                                         -len(name) if name.startswith(wanted) else len(name)))
            names = names[:64]
            if len(wanted) >= 3:
                names += get_close_matches(wanted, self.by_name, n=16, cutoff=.55)
            rows, seen = [], set()
            for name in dict.fromkeys(names):
                for record in self.by_name[name]:
                    identity = (record["title"], record["author"])
                    if identity not in seen:
                        seen.add(identity)
                        rows.append({"title": record["title"], "author": record["author"],
                                     "aliases": record["aliases"]})
        return rows
