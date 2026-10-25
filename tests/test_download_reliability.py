"""Download regressions reproduced with original local chapter fixtures."""
import gzip
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zlib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_content import chapter_problem, inspect_export, replace_exported, seed_checkpoints
from bookpack_core import Engine
from bookpack_platforms import WorkPlatforms
from bookpack_search import normalize, parse_titles
from bookpack_sources import Document, SourceCancelled, SourceManager

TITLE = "雨后借书处"
ORIGINAL = "原创可靠性样文：雨停以后，值班员将第二封回信放进旧木箱。\n"
PLACEHOLDER = ORIGINAL * 300 + "正在手打中，请稍等片刻，内容更新后，请重新刷新页面！"


class ReliabilityFixture(BaseHTTPRequestHandler):
    calls = Counter()
    fixed = False

    def log_message(self, *args):
        pass

    def respond(self, text, encoding="", mime="text/html; charset=utf-8"):
        raw = text.encode("utf-8")
        raw = gzip.compress(raw) if encoding == "gzip" else zlib.compress(raw) if encoding == "deflate" else raw
        self.send_response(200)
        self.send_header("Content-Type", mime)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path, query = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        self.calls[path] += 1
        if path == "/":
            return self.respond('<form action="/compressed-search"><input name="keyword"></form>', "gzip")
        if path == "/compressed-search":
            return self.respond(f'<a href="/good-book">{TITLE}</a>', "gzip")
        if path == "/deflated":
            return self.respond(ORIGINAL, "deflate")
        if path == "/search":
            return self.respond(f'<a href="/{query.get("book", "good")}-book">{TITLE}</a>')
        if path.endswith("-book"):
            kind = path[1:-5]
            urls = ("/pages/0.html", "/pages/1.html") if kind == "pages" else (f"/{kind}/1.html", f"/{kind}/2.html")
            return self.respond(f'<h1>{TITLE}</h1><p>作者：原创作者</p><ul class="toc"><li><a href="{urls[0]}">第一章 雨声</a></li><li><a href="{urls[1]}">第二章 回信</a></li></ul>')
        if path == "/pages/0.html":
            return self.respond('<div id="content">原创第一章上半。</div><a title="第二章 回信" href="0_2.html"><svg><path/></svg></a>')
        if path == "/pages/0_2.html":
            return self.respond('<div id="content">原创第一章下半。</div><a href="1.html"><svg><path/></svg></a>')
        if path == "/pages/1.html":
            return self.respond('<div id="content">原创第二章正文。</div>')
        if path in {"/bad/1.html", "/good/1.html"}:
            return self.respond('<div id="content">原创第一章正文。</div>')
        if path in {"/bad/2.html", "/good/2.html"}:
            body = PLACEHOLDER if path == "/bad/2.html" and not self.fixed else ORIGINAL
            return self.respond(f'<div id="content">{body}</div>')
        if path == "/original.txt":
            return self.respond(ORIGINAL, mime="text/plain; charset=utf-8")
        self.send_error(404)


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ReliabilityFixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.root = Path(__file__).resolve().parents[1] / ".functional-runs"
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.home = Path(self.temp.name).resolve()
        assert self.home.is_relative_to(self.root.resolve())
        self.engine = Engine(self.home)
        self.config([])
        ReliabilityFixture.calls.clear()
        ReliabilityFixture.fixed = False

    def tearDown(self):
        self.engine.cancel_event.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=15)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def source(self, kind):
        return {"id":kind, "name":kind, "url":self.base, "search":{"url":"/search", "params":{"book":kind,"q":"{title}"}},
                "content_selector":"#content", "chapter_selector":".toc a"}

    def config(self, sources, **fields):
        self.engine.search.save_settings({"local_dirs":[], "wikisource":False, "web_sources":sources, "books":[], **fields})

    def finish(self):
        self.engine.runner.join(timeout=12)
        self.assertFalse(self.engine.runner.is_alive())
        return self.engine.snapshot()["batches"][0]

    def test_gzip_search_form_and_deflate_are_decoded_before_charset(self):
        manager = self.engine.search.sources
        html, _ = manager.search_request({"url":self.base}, "雨后", threading.Event(), manager.session())
        self.assertIn(TITLE, html)
        self.assertIn(ORIGINAL, manager.fetch(self.base + "/deflated", threading.Event())[0])

    def test_icon_only_second_page_is_included(self):
        self.config([self.source("pages")])
        self.engine.create(parse_titles(TITLE))
        batch = self.finish()
        task = batch["tasks"][0]
        self.assertEqual((task["status"], task["chapter_count"], task["page_count"]), ("done", 2, 3))
        text = (self.engine.batch_directory(batch["id"]) / "书籍" / task["filename"]).read_text(encoding="utf-8")
        self.assertLess(text.index("第一章上半"), text.index("第一章下半"))
        self.assertLess(text.index("第一章下半"), text.index("第二章正文"))

    def test_placeholder_is_not_success_and_retry_reuses_good_chapter(self):
        self.config([self.source("bad")])
        self.engine.create(parse_titles(TITLE))
        batch = self.finish()
        self.assertEqual(batch["tasks"][0]["status"], "failed")
        self.assertIsNone(batch["archive"])
        self.assertIn("占位", batch["tasks"][0]["source_failures"][0]["error"])
        ReliabilityFixture.fixed = True
        self.engine.retry(batch["id"])
        changed = self.finish()["tasks"][0]
        self.assertEqual(changed["status"], "done")
        self.assertEqual(changed["resumed_chapters"], 1)
        self.assertEqual(ReliabilityFixture.calls["/bad/1.html"], 1)

    def test_matching_chapter_is_repaired_from_second_source(self):
        self.config([self.source("bad"), self.source("good")])
        lookup = self.engine.search.sources.lookup
        def indexed_headings(source, *args, **kwargs):
            book = lookup(source, *args, **kwargs)
            if source["id"] == "good" and book:
                book["chapters"] = [{**row, "title": str(index + 1) + "." + row["title"]}
                                    for index, row in enumerate(book["chapters"])]
            return book
        self.engine.search.sources.lookup = indexed_headings
        self.engine.create(parse_titles(TITLE + " | 原创作者"))
        batch = self.finish()
        task = batch["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(len(task["chapter_repairs"]), 1)
        self.assertEqual(task["chapter_repairs"][0]["source"], "good")
        text = (self.engine.batch_directory(batch["id"]) / "书籍" / task["filename"]).read_text(encoding="utf-8")
        self.assertNotIn("正在手打", text)
        self.assertIn(ORIGINAL.strip(), text)
        self.assertEqual(ReliabilityFixture.calls["/good/1.html"], 0)

    def test_cancelled_export_keeps_valid_checkpoint(self):
        source = self.source("good")
        manager = self.engine.search.sources
        task = manager.lookup({**source,"enabled":True}, TITLE, "", threading.Event())
        stop = threading.Event()
        cache = self.home / "cache"
        def progress(*args):
            stop.set()
        with self.assertRaises(SourceCancelled):
            manager.export(task, self.home / "partial.txt", stop, progress, cache_dir=cache)
        stop.clear()
        result = manager.export(task, self.home / "resumed.txt", stop, lambda *args:None, cache_dir=cache)
        self.assertEqual(result["resumed_chapters"], 1)
        self.assertEqual(ReliabilityFixture.calls["/good/1.html"], 1)

    def test_pagination_repair_prefetch_keeps_order_and_avoids_repeat_requests(self):
        manager = self.engine.search.sources
        task = manager.lookup({**self.source("pages"),"enabled":True}, TITLE, "", threading.Event())
        cache = self.home / "prefetched"
        manager.prefetch(task, threading.Event(), lambda *args:None, cache)
        metadata = manager.export(task, self.home / "repaired.txt", threading.Event(), lambda *args:None, cache_dir=cache)
        text = (self.home / "repaired.txt").read_text(encoding="utf-8")
        self.assertLess(text.index("第一章上半"), text.index("第一章下半"))
        self.assertLess(text.index("第一章下半"), text.index("第二章正文"))
        self.assertEqual(metadata["resumed_chapters"], 2)
        self.assertEqual(ReliabilityFixture.calls["/pages/0.html"], 1)
        self.assertEqual(ReliabilityFixture.calls["/pages/0_2.html"], 1)

    def test_legacy_placeholder_inspection_and_cache_seed(self):
        chapters = [{"title":"第一章 雨声","url":self.base+"/good/1.html"}, {"title":"第二章 回信","url":self.base+"/bad/2.html"}]
        task = {"chapters":chapters, "source_pages":[row["url"] for row in chapters]}
        text = TITLE + "\n\n第一章 雨声\n\n" + ORIGINAL + "\n\n第二章 回信\n\n" + PLACEHOLDER
        issues = inspect_export(task, text)
        self.assertEqual([row["title"] for row in issues], ["第二章 回信"])
        self.assertEqual(seed_checkpoints(task, text, self.home / "cache"), 1)
        partial = replace_exported(task, text, {1:ORIGINAL})
        self.assertNotIn("正在手打", partial)
        self.assertEqual(len(inspect_export(task, partial)), 0)
        self.assertEqual(inspect_export(task, TITLE)[0]["kind"], "structure")
        task["content_issues"] = [{"kind": "wrong_content"}]
        self.assertEqual(seed_checkpoints(task, partial, self.home / "bad-legacy-cache"), 0)

    def test_wrong_book_content_retry_changes_source_and_skips_old_cache(self):
        self.config([self.source("good")])
        self.engine.create(parse_titles(TITLE))
        batch = self.finish()
        task = batch["tasks"][0]
        task.update(source_id="bad", web_source=self.source("bad"),
                    content_issues=[{"kind":"wrong_content", "title":TITLE, "message":"已核实正文串入别的作品"}])
        self.config([{**self.source("bad"), "enabled":False}, self.source("good")])
        selected = self.engine.search.resolve_candidate({"download_kind":"web_candidate", "web_source":self.source("bad"),
                    "source_page":self.base+"/bad/book", "matched_title":TITLE}, "原创作者", threading.Event())
        self.assertEqual(selected["source_id"], "good")
        self.config([self.source("bad"), self.source("good")])
        self.engine.retry(batch["id"])
        repaired = self.finish()["tasks"][0]
        self.assertEqual(repaired["status"], "done")
        self.assertEqual(repaired["source_id"], "good")
        self.assertEqual(repaired["content_issues"], [])
        self.assertEqual(ReliabilityFixture.calls["/bad/2.html"], 0)

    def test_normal_author_note_is_not_a_placeholder(self):
        self.assertEqual(chapter_problem(ORIGINAL * 50 + "作者附记：下一章正在手打，请稍等。"), "")

    def test_explicit_toc_preserves_appendices_without_chapter_numbers(self):
        source = {**self.source("good"), "include_all_toc_entries":True}
        html = f'<h1>{TITLE}</h1><div class="toc"><a href="/good/1.html">第一章 雨声</a><a href="/good/2.html">完结感言</a></div><a href="/">首页</a>'
        book = self.engine.search.sources.book(source, self.base + "/book", TITLE, "", threading.Event(), document=Document(html).root)
        self.assertEqual([row["title"] for row in book["chapters"]], ["第一章 雨声", "完结感言"])

    def test_short_prefix_requires_choice_then_acquires_selected_book(self):
        self.assertEqual(self.engine.search.matcher.candidate("雨后借书", TITLE)["match_kind"], "prefix")
        self.config([], books=[{"title":TITLE,"author":"原创作者","url":self.base+"/original.txt"}])
        self.engine.create(parse_titles("雨后"))
        batch = self.finish()
        self.assertEqual(batch["status"], "needs_choice")
        task = batch["tasks"][0]
        self.assertEqual(task["candidates"][0]["match_kind"], "prefix")
        self.assertEqual(ReliabilityFixture.calls["/original.txt"], 0)
        self.engine.choose(batch["id"], task["id"], task["candidates"][0]["candidate_id"])
        self.assertEqual(self.finish()["tasks"][0]["status"], "done")

    def test_platform_prefix_can_resolve_full_title_after_choice(self):
        file = self.home / "platforms.json"
        file.write_text(json.dumps({"books":[{"title":TITLE,"author":"原创作者","platform":"起点","official_url":"https://m.qidian.com/book/1000000000/"}]}), encoding="utf-8")
        self.engine.search.platforms = WorkPlatforms(self.home, normalize, file)
        self.engine.create(parse_titles("雨后"))
        batch = self.finish()
        task = batch["tasks"][0]
        self.assertEqual(task["candidates"][0]["download_kind"], "catalog_candidate")
        self.config([self.source("good")])
        self.engine.choose(batch["id"], task["id"], task["candidates"][0]["candidate_id"])
        self.assertEqual(self.finish()["tasks"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
