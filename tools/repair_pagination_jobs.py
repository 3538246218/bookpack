"""Continue the two inspected pagination repairs through the running application."""
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[1]
origin = json.loads((root / "运行地址.json").read_text(encoding="utf-8-sig"))["url"]
def api(route, data=None):
    request = Request(origin + route, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type":"application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)

for batch_id in ("858427d702d0", "fa677fa4d2b0"):
    for attempt in range(10):
        try:
            api("api/retry", {"id":batch_id})
            break
        except HTTPError as exc:
            error = json.load(exc).get("error", "")
            if "收尾" not in error or attempt == 9:
                raise RuntimeError(error) from exc
            time.sleep(1)
    previous, reported = None, 0
    while True:
        batch = next(row for row in api("api/state")["batches"] if row["id"] == batch_id)
        task = batch["tasks"][0]
        summary = {"batch_id":batch_id,"title":task["title"],"status":batch["status"],
                   "task_status":task["status"],"chapters":task.get("chapter_count",0),
                   "expected":task.get("expected_chapters",0),"pages":task.get("page_count",0),
                   "issues":len(task.get("content_issues",[]))}
        signature = (summary["status"],summary["task_status"],summary["chapters"]//100)
        if signature != previous or time.monotonic()-reported > 30:
            print(json.dumps(summary,ensure_ascii=False),flush=True)
            previous, reported = signature, time.monotonic()
        if batch["status"] not in {"running","stopping","packing"}:
            print(json.dumps({"finished":summary,"error":task.get("error","")[:1200],
                              "archive":batch.get("archive")},ensure_ascii=False),flush=True)
            if batch["status"] == "cancelled":
                sys.exit(0)
            break
        time.sleep(2)
