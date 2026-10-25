"""Work-platform classification is independent of mirror/download-source identity."""
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_platforms import WorkPlatforms, official_record
from bookpack_search import normalize, parse_titles
from bookpack_sources import SourceManager


class OriginalFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        raw = "平台元数据功能测试的原创样文，不是真实小说正文。\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.platforms = WorkPlatforms(self.home, normalize)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalogue_classifies_title_and_author_and_old_title_alias(self):
        for title, author, expected in [("我不是戏神", "三九音域", "番茄"),
                ("神秘复苏", "佛前献花", "起点"), ("我有一座恐怖屋", "我会修空调", "起点")]:
            with self.subTest(title=title):
                result = self.platforms.annotate({"title": title, "author": author})
                self.assertEqual(result["platform_label"], expected)
                self.assertTrue(result["platform_verified"])
                self.assertTrue(result["platform_records"][0]["official_url"].startswith("https://"))

    def test_mirror_name_and_domain_do_not_classify_a_work(self):
        result = self.platforms.annotate({"title": "没有收录的原创作品", "author": "原创作者",
            "source": "番茄小说", "source_page": "https://mirror.example/起点/1", "platform_label": "起点", "platform_verified": True})
        self.assertEqual(result["platform_label"], "平台未核实")
        self.assertFalse(result["platform_verified"])
        self.assertIsNone(official_record("原创作品", "作者", "番茄", "https://mirror.example/page/123"))

    def test_same_title_different_or_missing_author_remains_unknown(self):
        for author in ("另一个作者", ""):
            result = self.platforms.annotate({"title": "神秘复苏", "author": author})
            self.assertFalse(result["platform_verified"])

    def test_new_official_records_persist_and_support_other_platforms(self):
        record = official_record("原创平台样文", "原创作者", "纵横", "https://www.zongheng.com/detail/123456.html", "悬疑")
        self.platforms.observe([{"title": "原创平台样文", "author": "原创作者", "platform_record": record}])
        restored = WorkPlatforms(self.home, normalize)
        result = restored.annotate({"title": "原创平台样文", "author": "原创作者"})
        self.assertEqual((result["platform_label"], result["platform_category"]), ("纵横", "悬疑"))
        self.assertTrue((self.home / "平台索引.json").is_file())

    def test_cross_platform_records_are_kept_as_multiple_records(self):
        rows = []
        for platform, url in [("起点", "https://m.qidian.com/book/12345/"), ("番茄", "https://fanqienovel.com/page/12345")]:
            record = official_record("原创多平台样文", "原创作者", platform, url)
            rows.append({"title": record["title"], "author": record["author"], "platform_record": record})
        self.platforms.observe(rows)
        result = self.platforms.annotate({"title": "原创多平台样文", "author": "原创作者"})
        self.assertEqual(result["platform_label"], "起点 / 番茄")
        self.assertEqual(len(result["platform_records"]), 2)

    def test_official_qidian_search_records_include_work_metadata(self):
        records = [{"bName": "神秘复苏", "bAuth": "佛前献花", "bid": 1012584111,
                    "cat": "悬疑灵异", "subCateName": "惊悚微恐", "isPub": 0}]
        state = {"pageContext": {"pageProps": {"pageData": {"bookInfo": {"records": records}}}}}
        html = '<script id="vite-plugin-ssr_pageContext" type="application/json">' + json.dumps(state) + '</script>'
        manager = SourceManager(self.home, normalize)
        with patch.object(manager, "fetch", return_value=(html, "https://m.qidian.com/search")) as fetch:
            found = manager.suggest({"id": "qidian", "name": "起点官方目录", "url": "https://m.qidian.com/", "kind": "qidian"}, "神秘", threading.Event())
        self.assertEqual(found[0]["platform_record"]["platform"], "起点")
        self.assertEqual(found[0]["download_kind"], "online_only")
        self.assertEqual(fetch.call_count, 1)

    def test_verified_webnovels_rank_ahead_of_unknown_prefix_matches(self):
        engine = Engine(self.home)
        engine.search.save_settings({"local_dirs": [], "wikisource": False, "web_sources": [],
            "books": [{"title": "我有书", "author": "原创作者", "url": "https://example.com/original.txt", "source": "番茄下载站"}]})
        try:
            rows = engine.suggest("我有")["suggestions"]
            self.assertTrue(rows[0]["platform_verified"])
            self.assertTrue(all(row["platform_label"] != "番茄下载站" for row in rows))
            exact = engine.suggest("我有书")["suggestions"][0]
            self.assertEqual((exact["title"], exact["platform_verified"]), ("我有书", False))
            engine.search.save_settings({"local_dirs": [], "wikisource": False, "web_sources": [],
                "books": [{"title": "我有一座蓝色房", "author": "原创作者", "url": "https://example.com/original.txt"}]})
            typo = engine.suggest("我有一座蓝色屋")["suggestions"][0]
            self.assertEqual(typo["title"], "我有一座蓝色房")
        finally:
            engine.suggestions.close()

    def test_task_snapshot_and_report_separate_platform_from_download_source(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), OriginalFixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        engine = Engine(self.home)
        try:
            engine.search.save_settings({"local_dirs": [], "wikisource": False, "web_sources": [],
                "books": [{"title": "神秘复苏", "author": "佛前献花", "source": "原创文件测试源",
                           "url": f"http://127.0.0.1:{server.server_port}/original.txt"}]})
            batch_id = engine.create(parse_titles("神秘复苏 | 佛前献花"))
            engine.runner.join(timeout=10)
            before = engine.state_file.read_bytes()
            task = engine.snapshot()["batches"][0]["tasks"][0]
            self.assertEqual((task["status"], task["source"], task["platform_label"]), ("done", "原创文件测试源", "起点"))
            self.assertEqual(before, engine.state_file.read_bytes())
            with zipfile.ZipFile(engine.archive_path(batch_id)) as archive:
                report = json.loads(archive.read("下载报告.json"))["books"][0]
            self.assertEqual((report["search_source"], report["platform_label"]), ("原创文件测试源", "起点"))
        finally:
            engine.suggestions.close()
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
