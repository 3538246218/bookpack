"""Input completion and read-only upstream title queries, using original fixtures."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_search import normalize, parse_titles
from bookpack_platforms import WorkPlatforms


class SuggestFixture(BaseHTTPRequestHandler):
    calls = []
    old_started = threading.Event()
    release_old = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query)).get("q", "")
        self.calls.append((parsed.path, query))
        if parsed.path == "/slow-search" and query == "雨后":
            self.old_started.set()
            self.release_old.wait(3)
        if parsed.path in {"/search", "/slow-search"}:
            text = '<a href="/book">雨后借书处</a>' if query in {"雨后", "雨后借", "雨后借书处"} else '<a href="/other">小镇来信收藏处</a>' if query == "小镇" else "没有结果"
        elif parsed.path == "/wiki":
            text = json.dumps({"query": {"search": [{"title": "雨后借书处"}]}})
        elif parsed.path == "/original.txt":
            text = "原创联想样文：雨后，借书处亮起了灯。\n"
        else:
            text = "不应读取的作品页面"
        raw = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class SuggestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SuggestFixture)
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
        self.home = Path(self.temp.name)
        self.engine = Engine(self.home)
        empty_catalog = self.home / "empty-platforms.json"
        empty_catalog.write_text('{"books": []}', encoding="utf-8")
        self.engine.search.platforms = WorkPlatforms(self.home, normalize, empty_catalog)
        self.config()
        SuggestFixture.calls.clear()
        SuggestFixture.old_started.clear()
        SuggestFixture.release_old.clear()

    def tearDown(self):
        SuggestFixture.release_old.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=10)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def config(self, **changes):
        return self.engine.search.save_settings({"local_dirs": [], "books": [], "wikisource": False, "web_sources": [], **changes})

    def book(self, title="雨后借书处", **fields):
        return {"title": title, "author": "原创作者", "url": self.base + "/original.txt", **fields}

    def source(self, path="/search"):
        return {"id": "original", "name": "原创联想书源", "url": self.base,
                "search": {"url": path, "params": {"q": "{title}"}}}

    def test_short_prefix_completion_alias_and_local_filename(self):
        library = self.home / "本地书库"
        library.mkdir()
        (library / "小镇来信收藏处 - 原创作者_全本.txt").write_text("原创样文", encoding="utf-8")
        self.config(local_dirs=[str(library)], books=[self.book(aliases=["旧日雨声"]), self.book("雨后借书所")])
        found = self.engine.suggest("雨后")["suggestions"]
        self.assertEqual({row["title"] for row in found}, {"雨后借书处", "雨后借书所"})
        self.assertTrue(all(row["suggestion_kind"] == "prefix" for row in found))
        self.assertEqual(self.engine.suggest("旧日")["suggestions"][0]["title"], "雨后借书处")
        self.assertEqual(self.engine.suggest("小镇")["suggestions"][0]["author"], "原创作者")

    def test_typo_suggestions_and_author_distinction(self):
        self.config(books=[self.book(author="甲"), self.book(author="乙")])
        result = self.engine.suggest("雨后借书所")["suggestions"]
        self.assertEqual({row["author"] for row in result}, {"甲", "乙"})
        self.assertTrue(all(row["suggestion_kind"] == "fuzzy" for row in result))
        self.assertEqual([row["author"] for row in self.engine.suggest("雨后 | 乙")["suggestions"]], ["乙"])

    def test_long_prefix_completions_rank_first_but_exact_title_stays_first(self):
        long_title = "雨后小镇借书处的最后一封回信"
        self.config(books=[self.book("雨后书屋"), self.book("雨后借书处"), self.book(long_title)])
        rows = self.engine.suggest("雨后")["suggestions"]
        self.assertEqual([row["title"] for row in rows], [long_title, "雨后借书处", "雨后书屋"])
        self.assertGreater(rows[0]["suggestion_score"], rows[-1]["suggestion_score"])
        self.config(books=[self.book("雨后"), self.book(long_title), self.book("雨后书屋", author="另一作者")])
        self.assertEqual(self.engine.suggest("雨后")["suggestions"][0]["title"], "雨后")
        self.assertEqual([row["author"] for row in self.engine.suggest("雨后 | 原创作者")["suggestions"]],
                         ["原创作者", "原创作者"])
        self.assertLess(self.engine.search.matcher.match(long_title[:-1] + "音", long_title)["match_score"], 1)

    def test_prefix_alias_length_uses_normalized_matching_name(self):
        alias = "雨后小镇借书处的最后一封回信"
        self.config(books=[self.book("新书名", aliases=["雨后书屋", alias]), self.book("雨后借书处")])
        first = self.engine.suggest("雨后")["suggestions"][0]
        self.assertEqual((first["title"], first["matched_on"]), ("新书名", alias))
        self.config(books=[self.book("雨后！书！屋！！！"), self.book("雨后借书处")])
        self.assertEqual(self.engine.suggest("雨后")["suggestions"][0]["title"], "雨后借书处")

    def test_catalog_recall_keeps_long_prefixes_before_the_limit(self):
        titles = ["雨后" + "小镇" * index for index in range(1, 71)]
        catalog = self.home / "large-platforms.json"
        catalog.write_text(json.dumps({"books": [
            {"title":title, "author":"目录作者", "platform":"起点",
             "official_url":f"https://m.qidian.com/book/{1000000000 + index}/"}
            for index, title in enumerate([*titles, "雨后"])
        ]}, ensure_ascii=False), encoding="utf-8")
        self.engine.search.platforms = WorkPlatforms(self.home, normalize, catalog)
        rows = self.engine.suggest("雨后")["suggestions"]
        self.assertEqual(rows[0]["title"], "雨后")
        self.assertEqual([row["title"] for row in rows[1:]], list(reversed(titles[-7:])))

    def test_suggesting_does_not_create_tasks_or_download_files(self):
        self.config(books=[self.book()], web_sources=[self.source()])
        before = self.engine.snapshot()
        state_before = self.engine.state_file.read_bytes()
        result = self.engine.suggest("雨后", online=True)
        self.assertEqual(result["suggestions"][0]["title"], "雨后借书处")
        self.assertEqual(before, self.engine.snapshot())
        self.assertEqual(state_before, self.engine.state_file.read_bytes())
        self.assertEqual(list(self.engine.output.iterdir()), [])
        self.assertTrue(all(path == "/search" for path, _ in SuggestFixture.calls))

    def test_online_typo_fallback_and_cache(self):
        self.config(web_sources=[self.source()])
        result = self.engine.suggest("雨后借书所", online=True)
        self.assertEqual(result["suggestions"][0]["suggestion_kind"], "fuzzy")
        self.assertIn(("/search", "雨后借"), SuggestFixture.calls)
        calls = list(SuggestFixture.calls)
        cached = self.engine.suggest("雨后借书所", online=True)
        self.assertEqual(result["suggestions"], cached["suggestions"])
        self.assertEqual(calls, SuggestFixture.calls)

    def test_recently_acquired_titles_are_available_after_catalog_removal(self):
        self.config(books=[self.book()])
        self.engine.create(parse_titles("雨后借书处"))
        self.engine.runner.join(timeout=10)
        self.assertEqual(self.engine.snapshot()["batches"][0]["status"], "complete")
        self.config()
        self.assertEqual(self.engine.suggest("雨后")["suggestions"][0]["title"], "雨后借书处")

    def test_old_online_query_cannot_replace_new_generation(self):
        self.config(web_sources=[self.source("/slow-search")])
        previous = []
        thread = threading.Thread(target=lambda: previous.append(self.engine.suggest("雨后", True, "client", 1)))
        thread.start()
        self.assertTrue(SuggestFixture.old_started.wait(2))
        current = self.engine.suggest("小镇", True, "client", 2)
        SuggestFixture.release_old.set()
        thread.join(timeout=5)
        self.assertEqual(current["suggestions"][0]["title"], "小镇来信收藏处")
        self.assertTrue(previous[0]["cancelled"])

    def test_wikisource_prefix_and_empty_input(self):
        self.config(wikisource=True)
        self.engine.search.wiki_api = self.base + "/wiki"
        self.assertEqual(self.engine.suggest("雨后", True)["suggestions"][0]["source"], "维基文库")
        self.assertEqual(self.engine.suggest("")["suggestions"], [])
        self.assertEqual(self.engine.suggest("雨")["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
