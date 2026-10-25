"""Focused matching, candidate selection and original-text packaging checks."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_match import TitleMatcher
from bookpack_search import SearchCandidates, normalize, parse_titles

ORIGINAL = "原创匹配样文：雨后，借书处收到了小镇最后一封回信。\n"
LONG_TITLE = "雨后小镇借书处的最后一封回信"
LONG_TYPO = "雨后小镇借书处的最后一封回音"
OTHER_TITLE = "雨后小镇借书处的另一封信"
ALTERNATIVE = "另一部原创样文：海边的旧邮局重新开门了。\n"


class MatchingFixture(BaseHTTPRequestHandler):
    queries = []
    downloads = []

    def log_message(self, *args):
        pass

    def respond(self, value, mime="text/html; charset=utf-8"):
        raw = value.encode()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(path.query))
        if path.path == "/search":
            self.queries.append(query.get("q"))
            # Full misspelled queries return no results, while a prefix works.
            if query.get("q") == "雨后借":
                return self.respond('<a href="/book">雨后借书处</a>')
            return self.respond("<p>没有结果</p>")
        if path.path == "/exact-search":
            return self.respond('<a href="/book">雨后借书处</a>')
        if path.path == "/long-search":
            return self.respond(f'<a href="/long-book">{LONG_TITLE}</a>')
        if path.path == "/wrong-search":
            return self.respond(f'<a href="/book">{LONG_TITLE}</a>')
        if path.path == "/broken-long-search":
            return self.respond(f'<a href="/broken-long-book">{LONG_TITLE}</a>')
        if path.path == "/broken-long-book":
            return self.respond(f'<h1>{LONG_TITLE}</h1><p>作者：原创作者</p><a href="/not-a-book.txt">TXT 下载</a>')
        if path.path == "/not-a-book.txt":
            return self.respond("<html><body>无可用书籍</body></html>")
        if path.path in {"/book", "/long-book"}:
            title = LONG_TITLE if path.path == "/long-book" else "雨后借书处"
            return self.respond(f'<h1>{title}</h1><p>作者：原创作者</p><a href="/original.txt">TXT 下载</a>')
        if path.path == "/original.txt":
            self.downloads.append(path.path)
            return self.respond(ORIGINAL, "text/plain; charset=utf-8")
        if path.path == "/alternative.txt":
            self.downloads.append(path.path)
            return self.respond(ALTERNATIVE, "text/plain; charset=utf-8")
        if path.path in {"/slow-original.txt", "/slow-sibling.txt"}:
            self.downloads.append(path.path)
            chunk = (ORIGINAL * 2048).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(chunk) * 20))
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(.05)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        if path.path == "/api/author/search/search_book/v1":
            return self.respond(json.dumps({"code": 0, "data": {"search_book_data_list": [
                {"book_name": "雨后借书处", "author": "原创作者", "book_id": "123"}]}}), "application/json")
        if path.path == "/wiki":
            if query.get("list") == "search":
                result = {"query": {"search": [{"title": "雨后借书处"}]}}
            elif query.get("action") == "parse":
                result = {"parse": {"text": {"*": f"<p>{ORIGINAL}</p>"}}}
            elif query.get("titles") == "雨后借书处":
                result = {"query": {"pages": {"1": {"ns": 0, "pageid": 1, "title": "雨后借书处"}}}}
            else:
                result = {"query": {"pages": {"-1": {"missing": ""}}}}
            return self.respond(json.dumps(result), "application/json")
        self.send_error(404)


class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MatchingFixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.root = Path(__file__).resolve().parents[1] / ".functional-runs"
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.root)
        self.home = Path(self.temporary.name)
        self.engine = Engine(self.home)
        self.config()
        MatchingFixture.queries.clear()
        MatchingFixture.downloads.clear()
        self.matcher = TitleMatcher(normalize)

    def tearDown(self):
        if self.engine.runner:
            self.engine.runner.join(timeout=15)
        self.temporary.cleanup()

    def config(self, **changes):
        settings = {"local_dirs": [], "wikisource": False, "web_sources": [], "books": [], **changes}
        self.engine.search.save_settings(settings)

    def book(self, title, author="原创作者", **fields):
        return {"title": title, "author": author, "url": self.base + "/original.txt", **fields}

    def source(self, search, **fields):
        return {"id": "original", "name": "原创测试书源", "url": self.base,
                "search": {"url": search, "params": {"q": "{title}"}}, **fields}

    def run_titles(self, titles, engine=None):
        engine = engine or self.engine
        engine.create(parse_titles(titles))
        return self.finish(engine)

    def finish(self, engine=None):
        engine = engine or self.engine
        engine.runner.join(timeout=15)
        self.assertFalse(engine.runner.is_alive())
        return engine.snapshot()["batches"][0]

    def wait_task(self, index, status):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            batch = self.engine.snapshot()["batches"][0]
            if batch["tasks"][index]["status"] == status:
                return batch
            time.sleep(.01)
        self.fail(f"task {index} did not reach {status}")

    def switch_to_other(self, batch):
        task = batch["tasks"][0]
        candidate = next(row for row in task["candidates"] if row["matched_title"] == OTHER_TITLE)
        self.engine.choose(batch["id"], task["id"], candidate["candidate_id"])

    def test_punctuation_script_alias_and_spelling_errors(self):
        self.assertEqual(self.matcher.match("《雨后，借书处》", "雨后借书处")["match_kind"], "exact")
        self.assertEqual(self.matcher.match("旧书名", "新书名", ["旧书名"])["match_kind"], "alias")
        self.assertEqual(self.matcher.match("ＥＢＯＯＫ", "ebook")["match_kind"], "exact")
        for query in ("我有一座恐布屋", "我有座恐怖屋", "我有一座怖恐屋"):
            with self.subTest(query=query):
                match = self.matcher.match(query, "我有一座恐怖屋")
                self.assertIsNotNone(match)
                self.assertLess(match["match_score"], .90)
        self.assertIsNone(self.matcher.match("科幻小故事", "山海经"))
        self.assertIsNone(self.matcher.match("诡密", "诡秘"))

    def test_unique_high_similarity_continues_with_canonical_filename(self):
        self.config(books=[self.book(LONG_TITLE)])
        batch = self.run_titles(LONG_TYPO)
        self.assertEqual(batch["status"], "complete")
        task = batch["tasks"][0]
        self.assertEqual(task["title"], LONG_TYPO)
        self.assertEqual(task["matched_title"], LONG_TITLE)
        self.assertEqual(task["match_confidence"], "high")
        self.assertIn(LONG_TITLE, task["filename"])
        self.assertEqual(len(task["candidates"]), 1)
        self.assertEqual(task["selected_candidate_id"], task["candidates"][0]["candidate_id"])

    def test_automatic_candidates_survive_restart_and_can_replace_completed_book(self):
        self.config(books=[self.book(LONG_TITLE), self.book(OTHER_TITLE, url=self.base + "/alternative.txt"),
                           self.book("并行原创样文")])
        batch = self.run_titles(LONG_TYPO + "\n并行原创样文")
        self.assertEqual(batch["tasks"][0]["match_confidence"], "high")
        old_hash = batch["tasks"][1]["sha256"]
        self.engine = Engine(self.home)
        self.switch_to_other(self.engine.snapshot()["batches"][0])
        changed = self.finish()
        self.assertEqual(changed["status"], "complete")
        self.assertEqual(changed["tasks"][0]["matched_title"], OTHER_TITLE)
        self.assertEqual(changed["tasks"][0]["match_confidence"], "selected")
        self.assertEqual(len(changed["tasks"][0]["candidates"]), 2)
        self.assertEqual(changed["tasks"][1]["sha256"], old_hash)
        with zipfile.ZipFile(self.engine.archive_path(batch["id"])) as archive:
            books = [name for name in archive.namelist() if name.startswith("书籍/")]
            self.assertEqual(len(books), 2)
            self.assertFalse(any(LONG_TITLE in name for name in books))
            self.assertEqual(archive.read("书籍/" + changed["tasks"][0]["filename"]).decode(), ALTERNATIVE)

    def test_switch_during_download_only_stops_the_selected_task(self):
        self.config(books=[self.book(LONG_TITLE, url=self.base + "/slow-original.txt"),
                           self.book(OTHER_TITLE, url=self.base + "/alternative.txt"),
                           self.book("并行原创样文", url=self.base + "/slow-sibling.txt")])
        self.engine.create(parse_titles(LONG_TYPO + "\n并行原创样文"), concurrency=2)
        batch = self.wait_task(0, "downloading")
        self.assertEqual(len(batch["tasks"][0]["candidates"]), 2)
        self.switch_to_other(batch)
        changed = self.finish()
        self.assertEqual(changed["status"], "complete")
        self.assertEqual([task["status"] for task in changed["tasks"]], ["done", "done"])
        self.assertEqual(changed["tasks"][0]["matched_title"], OTHER_TITLE)
        self.assertEqual(changed["tasks"][0]["match_confidence"], "selected")
        self.assertEqual(MatchingFixture.downloads.count("/slow-sibling.txt"), 1)
        self.assertFalse(self.engine.cancel_event.is_set())
        self.assertFalse(list(self.engine.batch_directory(batch["id"]).glob("*.part")))

    def test_pending_reselection_recovers_after_interruption(self):
        self.config(books=[self.book(LONG_TITLE), self.book(OTHER_TITLE, url=self.base + "/alternative.txt")])
        self.run_titles(LONG_TYPO)
        batch = self.engine.batches[0]
        task = batch["tasks"][0]
        task["pending_candidate"] = next(row for row in task["candidates"] if row["matched_title"] == OTHER_TITLE)
        task["status"], batch["status"] = "downloading", "running"
        self.engine._save()
        self.engine = Engine(self.home)
        restored = self.engine.snapshot()["batches"][0]
        self.assertEqual(restored["status"], "interrupted")
        self.assertFalse(restored["tasks"][0].get("pending_candidate"))
        self.assertEqual(restored["tasks"][0]["selected_candidate"]["matched_title"], OTHER_TITLE)
        self.engine.retry(batch["id"])
        self.assertEqual(self.finish()["tasks"][0]["matched_title"], OTHER_TITLE)

    def test_completed_task_can_be_reselected_while_sibling_is_still_running(self):
        self.config(books=[self.book(LONG_TITLE), self.book(OTHER_TITLE, url=self.base + "/alternative.txt"),
                           self.book("并行原创样文", url=self.base + "/slow-sibling.txt")])
        self.engine.create(parse_titles(LONG_TYPO + "\n并行原创样文"), concurrency=2)
        batch = self.wait_task(0, "done")
        self.assertEqual(batch["status"], "running")
        self.switch_to_other(batch)
        changed = self.finish()
        self.assertEqual(changed["status"], "complete")
        self.assertEqual(changed["tasks"][0]["matched_title"], OTHER_TITLE)
        self.assertEqual(MatchingFixture.downloads.count("/slow-sibling.txt"), 1)

    def test_close_scores_and_sequels_require_choice(self):
        self.config(books=[self.book(LONG_TITLE), self.book("雨后小镇借书处的最后一封回函")])
        batch = self.run_titles(LONG_TYPO)
        self.assertEqual(batch["status"], "needs_choice")
        self.assertEqual(len(batch["tasks"][0]["candidates"]), 2)
        self.config(books=[self.book(LONG_TITLE + "续")])
        self.assertEqual(self.run_titles(LONG_TITLE)["status"], "needs_choice")
        self.config(books=[self.book(LONG_TITLE + "第二部")])
        self.assertEqual(self.run_titles(LONG_TITLE + "第一部")["status"], "needs_choice")

    def test_exact_local_result_wins_over_fuzzy_catalog(self):
        library = self.home / "本地书库"
        library.mkdir()
        (library / (LONG_TYPO + ".txt")).write_text(ORIGINAL, encoding="utf-8")
        self.config(local_dirs=[str(library)], books=[self.book(LONG_TITLE)])
        task = self.run_titles(LONG_TYPO)["tasks"][0]
        self.assertEqual((task["source"], task["matched_title"]), ("本地书库", LONG_TYPO))

    def test_local_choice_survives_restart_and_preserves_completed_sibling(self):
        library = self.home / "本地书库"
        library.mkdir()
        (library / "雨后借书处 - 原创作者.txt").write_text(ORIGINAL, encoding="utf-8")
        self.config(local_dirs=[str(library)], books=[self.book("另一部原创作品")])
        batch = self.run_titles("另一部原创作品\n雨后借书所")
        self.assertEqual([task["status"] for task in batch["tasks"]], ["done", "needs_choice"])
        original_hash = batch["tasks"][0]["sha256"]
        restored = Engine(self.home)
        self.engine = restored
        waiting = restored.snapshot()["batches"][0]["tasks"][1]
        restored.choose(batch["id"], waiting["id"], waiting["candidates"][0]["candidate_id"])
        chosen = self.finish(restored)
        self.assertEqual(chosen["status"], "complete")
        self.assertEqual(chosen["tasks"][0]["sha256"], original_hash)
        self.assertEqual(chosen["tasks"][1]["match_confidence"], "selected")
        with zipfile.ZipFile(restored.archive_path(batch["id"])) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("书籍/")]), 2)
            report = json.loads(archive.read("下载报告.json"))
            self.assertEqual(report["books"][1]["matched_title"], "雨后借书处")
            self.assertEqual(report["books"][1]["title"], "雨后借书所")

    def test_same_title_authors_are_selectable_and_author_filter_stays_exact(self):
        self.config(books=[self.book("同名作品", "甲"), self.book("同名作品", "乙")])
        batch = self.run_titles("同名作品")
        candidates = batch["tasks"][0]["candidates"]
        self.assertEqual({item["matched_author"] for item in candidates}, {"甲", "乙"})
        self.engine.choose(batch["id"], batch["tasks"][0]["id"], candidates[1]["candidate_id"])
        self.assertEqual(self.finish()["tasks"][0]["matched_author"], "乙")
        self.config(books=[self.book("雨后借书处", "甲")])
        self.assertEqual(self.run_titles("雨后借书所 | 乙")["tasks"][0]["status"], "failed")

    def test_empty_online_search_uses_keyword_candidate_then_downloads(self):
        self.config(web_sources=[self.source("/search")])
        batch = self.run_titles("雨后借书所")
        self.assertEqual(batch["status"], "needs_choice")
        self.assertIn("雨后借", MatchingFixture.queries)
        self.assertLessEqual(len(MatchingFixture.queries), 3)
        task = batch["tasks"][0]
        self.engine.choose(batch["id"], task["id"], task["candidates"][0]["candidate_id"])
        task = self.finish()["tasks"][0]
        self.assertEqual((task["status"], task["matched_author"]), ("done", "原创作者"))

    def test_exact_online_result_wins_over_local_fuzzy(self):
        self.config(books=[self.book("雨后借书所")], web_sources=[self.source("/exact-search")])
        task = self.run_titles("雨后借书处")["tasks"][0]
        self.assertEqual((task["source"], task["match_kind"]), ("原创测试书源", "exact"))

    def test_high_online_match_checks_actual_book_metadata(self):
        self.config(web_sources=[self.source("/long-search")])
        task = self.run_titles(LONG_TYPO)["tasks"][0]
        self.assertEqual(task["match_confidence"], "high")
        self.assertEqual(len(task["candidates"]), 1)
        self.assertEqual(task["candidates"][0]["matched_author"], "原创作者")
        self.config(web_sources=[self.source("/wrong-search")])
        batch = self.run_titles(LONG_TYPO)
        self.assertEqual(batch["status"], "needs_choice")
        self.assertIsNone(batch["archive"])

    def test_wikisource_candidates_resolve_through_canonical_title(self):
        self.config(wikisource=True)
        self.engine.search.wiki_api = self.base + "/wiki"
        batch = self.run_titles("雨后借书所")
        task = batch["tasks"][0]
        self.assertEqual(task["candidates"][0]["source"], "维基文库")
        self.engine.choose(batch["id"], task["id"], task["candidates"][0]["candidate_id"])
        self.assertEqual(self.finish()["status"], "complete")

    def test_source_switch_retains_original_fuzzy_score(self):
        self.config(web_sources=[self.source("/broken-long-search"), self.source("/long-search", id="second")])
        task = self.run_titles(LONG_TYPO)["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["source_id"], "second")
        self.assertEqual(task["match_kind"], "fuzzy")
        self.assertLess(task["match_score"], 1)
        self.assertEqual(task["title"], LONG_TYPO)

    def test_official_reading_candidates_remain_reading_links(self):
        self.config(web_sources=[self.source("/", kind="fanqie")])
        task = self.run_titles("雨后借书所")["tasks"][0]
        self.assertEqual(task["status"], "needs_choice")
        self.assertEqual(task["candidates"][0]["download_kind"], "online_only")

    def test_mirrors_are_grouped_and_candidate_list_is_bounded(self):
        self.config(books=[self.book("雨后借书处", source="甲源"), self.book("雨后借书处", source="乙源")]
                    + [self.book("雨后借书处" + str(index)) for index in range(12)])
        with self.assertRaises(SearchCandidates) as caught:
            self.engine.search.resolve("雨后借书所", "", threading.Event())
        candidates = caught.exception.candidates
        self.assertEqual(sum(item["matched_title"] == "雨后借书处" for item in candidates), 1)
        self.assertLessEqual(len(candidates), 8)


if __name__ == "__main__":
    unittest.main()
