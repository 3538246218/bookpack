"""Refresh only title/file metadata from the public catalog; never download novels."""
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bookpack_lightnovel import CATALOG_FILE, CATALOG_URL, catalog_entries
from bookpack_search import normalize
from bookpack_sources import Document, SourceManager

if __name__ == "__main__":
    if len(sys.argv) > 1:
        html = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        html, _ = SourceManager(ROOT, normalize).fetch(CATALOG_URL, threading.Event())
    nodes = Document(html).root.select("#data")
    if not nodes:
        raise ValueError("轻小说目录结构已改变，没有写入新目录。")
    data = json.loads("".join(value for value in nodes[0].children if isinstance(value, str)))
    books = catalog_entries(data)
    snapshot = {"source": "轻小说文库第三方公开整理目录", "catalog_url": CATALOG_URL,
                "project_url": "https://github.com/mojimoon/wenku8",
                "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "note": "只保存书名、作者、别名与公开文件链接；文件版本、可用性和卷数不由目录更新日期保证。",
                "books": books}
    CATALOG_FILE.parent.mkdir(exist_ok=True)
    temporary = CATALOG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(CATALOG_FILE)
    print(json.dumps({"path": str(CATALOG_FILE), "books": len(books),
                      "with_file_link": sum(bool(row["url"]) for row in books)}, ensure_ascii=False))
