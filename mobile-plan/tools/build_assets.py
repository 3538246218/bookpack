"""Build a self-contained mobile prototype from bundled public metadata only."""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import DEMO_TEXT
from vendor.fanqie_radar import analysis, schema

plan = ROOT / "mobile-plan"
prototype = plan / "prototype"
assets = prototype / "assets"
assets.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "web/ocean/poster.png", assets / "ocean.png")
boards = {}
fields = ("book_id", "title", "author", "category", "reads", "status", "rank_pos")
for channel in ("male", "female"):
    for rank in ("read", "new"):
        source = ROOT / f"data/radar/fanqie_{channel}_{rank}_ranks_20261013.json"
        books = schema.load_books(source)
        boards[f"{channel}/{rank}"] = {
            "date": "20261013", "items": [{key: book.get(key) for key in fields} for book in books],
            "heat": analysis.category_heat(books)[:6], "hotwords": analysis.hotwords(books, top=8),
        }
payload = {"boards": boards, "demoText": DEMO_TEXT}
(prototype / "radar-data.js").write_text(
    "// Generated from bundled snapshots; no personal tasks or downloaded novels.\n"
    + "window.MOBILE_PREVIEW_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
    encoding="utf-8",
)
print(json.dumps({"boards": {key: len(value["items"]) for key, value in boards.items()},
                  "total_rows": sum(len(value["items"]) for value in boards.values())}, ensure_ascii=False))
