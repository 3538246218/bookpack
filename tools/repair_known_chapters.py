"""Repair only confirmed placeholder chapters, retaining original exports and ZIPs."""
import argparse
import json
import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bookpack_content import read_checkpoint, replace_exported, seed_checkpoints, write_checkpoint
from bookpack_core import Engine, check_file, file_hash, now, safe_name

parser = argparse.ArgumentParser()
parser.add_argument("--home", type=Path, default=ROOT)
parser.add_argument("--batch", action="append", required=True)
args = parser.parse_args()
engine = Engine(args.home)
stop = threading.Event()
for batch_id in args.batch:
    batch = engine._batch(batch_id)
    directory = engine.batch_directory(batch_id)
    results = []
    for task in batch["tasks"]:
        issues = task.get("content_issues", [])
        if not issues or any(row["kind"] != "placeholder" for row in issues):
            continue
        file = directory / "书籍" / task["filename"]
        cache = directory / "章节缓存" / task["id"]
        original = file.read_text(encoding="utf-8")
        seeded = seed_checkpoints(task, original, cache)
        repair = engine.search.chapter_repair(task, stop)
        remaining, repaired, replacements, provenance_rows = [], 0, {}, []
        for issue in issues:
            chapter = task["chapters"][issue["index"]]
            provenance = None
            try:
                try:
                    text, pages = engine.search.sources.chapter(task, chapter, stop)
                except Exception as exc:
                    text, pages, provenance = repair(chapter, exc)
                provenance = provenance or {"title":chapter["title"], "source":task.get("source", ""),
                    "original_url":chapter["url"], "replacement_url":chapter["url"], "reason":"来源已恢复，重新获取占位章节"}
                write_checkpoint(cache, chapter, text, pages, provenance)
                replacements[issue["index"]] = text
                provenance_rows.append(provenance)
                repaired += 1
            except Exception as exc:
                remaining.append({**issue, "repair_error":str(exc)})
            if (repaired + len(remaining)) % 5 == 0 or issue is issues[-1]:
                print(json.dumps({"title":task["title"],"repaired":repaired,"checked":repaired+len(remaining),"total":len(issues)},ensure_ascii=False),flush=True)
        if repaired:
            backup = directory / "修复前备份"
            backup.mkdir(exist_ok=True)
            if not (backup / file.name).exists():
                shutil.copy2(file, backup / file.name)
            for archive in {batch.get("archive"), safe_name(batch["name"]) + ".zip"} - {None}:
                if (directory / archive).is_file() and not (backup / archive).exists():
                    shutil.copy2(directory / archive, backup / archive)
            temporary = directory / (task["id"] + ".repair.part")
            if not remaining:
                metadata = engine.search.sources.export(task, temporary, stop, lambda *args:None, cache_dir=cache)
            else:
                temporary.write_text(replace_exported(task, original, replacements), encoding="utf-8")
                pages = []
                for chapter in task["chapters"]:
                    row = read_checkpoint(cache, chapter)
                    pages.extend(row["pages"] if row else [chapter["url"]])
                metadata = {"source_pages":pages, "page_count":len(pages), "content_issues":remaining,
                            "chapter_repairs":task.get("chapter_repairs", []) + provenance_rows,
                            "resumed_chapters":seeded}
            source_sha = file_hash(temporary)
            info = check_file(temporary, "text/plain", "utf-8")
            temporary.replace(file)
            task.update(**metadata, **info, status="done", error="", sha256=file_hash(file), source_sha256=source_sha,
                        bytes=file.stat().st_size, total=file.stat().st_size, finished_at=now())
        else:
            task["content_issues"] = remaining
        results.append({"title":task["title"],"seeded_chapters":seeded,"repaired":repaired,"remaining":len(remaining),
                        "examples":[{"title":row["title"],"error":row.get("repair_error")} for row in remaining[:2]]})
    batch["status"] = "partial" if any(row.get("content_issues") or row["status"] != "done" for row in batch["tasks"]) else "complete"
    engine._package(batch)
    engine._save()
    print(json.dumps({"batch_id":batch_id,"results":results},ensure_ascii=False),flush=True)
engine.suggestions.close()
