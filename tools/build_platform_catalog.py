"""Build a small work-metadata catalog from local snapshots and official search records."""
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bookpack_platforms import official_record
from bookpack_sources import SourceManager
from bookpack_search import normalize

sys.stdout.reconfigure(encoding="utf-8")
rows, snapshots = [], []
for path in sorted((ROOT.parent / "fanqie-radar" / "data").glob("fanqie_*_ranks_20261013.json")):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    snapshots.append(path.name)
    for category in data.get("categories", []):
        for book in category.get("books", []):
            record = official_record(book.get("title"), book.get("author"), "番茄", book.get("url", ""),
                book.get("category") or category.get("name", ""), evidence="official_rank_snapshot:2026-10-13")
            if record:
                rows.append(record)
manager = SourceManager(ROOT, normalize)
queries = ["我有一座", "神秘复苏", "我的治愈系游戏", "诡秘之主", "怪谈游戏设计师"]
source = {"id": "qidian", "name": "起点官方作品目录", "kind": "qidian", "url": "https://m.qidian.com/"}
for query in queries:
    found = manager.suggest(source, query, threading.Event())
    for row in found:
        record = row.get("platform_record")
        if record:
            if record["title"] == "我有一座冒险屋" and record["author"] == "我会修空调":
                record["aliases"] = ["我有一座恐怖屋"]
            rows.append(record)
unique = {(row["title"], row["author"], row["platform"]): row for row in rows}
target = ROOT / "data" / "platform_catalog.json"
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps({"updated_at": "2026-10-24", "meaning": "作品平台记录，不代表下载来源或独家首发",
    "provenance": {"fanqie_snapshots": snapshots, "qidian_queries": queries}, "books": list(unique.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"file": str(target), "books": len(unique), "fanqie": sum(row["platform"] == "番茄" for row in unique.values()),
    "qidian": sum(row["platform"] == "起点" for row in unique.values())}, ensure_ascii=False))
